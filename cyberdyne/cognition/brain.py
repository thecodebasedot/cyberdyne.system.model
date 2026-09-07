"""Brain: the deliberative loop, 2 Hz.

Blackboard is rebuilt every tick from the bus, then a behaviour tree decides:

    Selector
    +-- Sequence[estop engaged        -> stay idle]
    +-- Sequence[battery critical/low -> go to charger, wait until full]
    +-- Sequence[charging & not full  -> keep charging]
    +-- Sequence[has patrol route     -> next waypoint]
    +-- Action[idle]

The tree publishes ``nav/goal`` and drives the system state machine. The
``Planner`` is consulted for free-form goals arriving on ``brain/goal``.
"""
from __future__ import annotations

from ..kernel.context import Context
from ..kernel.module import Module
from ..kernel.state import SystemState
from .behavior_tree import Action, Condition, Selector, Sequence, Status
from .constitution import Constitution
from .council import Council, Decision
from .planner import Plan, Planner, RulePlanner
from .simulate import MentalSimulator


class Brain(Module):
    """Deliberative loop. Free-form goals go through the council (plan ->
    constitution -> critic); only approved decisions become plans. Anything
    the council is unsure about becomes a question on ``brain/question`` and
    waits for ``human/answer``.
    """
    name = "brain"
    rate_hz = 2.0
    priority = 50

    def __init__(self, planner: Planner | None = None) -> None:
        super().__init__()
        self.planner = planner or RulePlanner()
        self.council: Council | None = None
        self.pending: Decision | None = None
        self.pending_since = 0.0

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        cfg = ctx.config.brain
        self.patrol = list(cfg.patrol)
        self.patrol_idx = 0
        self.laps = 0
        self.mode = "boot"
        self.current_goal: dict | None = None
        self.plan: Plan | None = None
        self.plan_idx = 0
        self._arrived = False
        self._subs = [ctx.bus.subscribe("nav/arrived", self._on_arrived, name="brain.arrived"),
                      ctx.bus.subscribe("brain/goal", self._on_goal, name="brain.goal"),
                      ctx.bus.subscribe("human/answer", self._on_answer, name="brain.answer"),
                      ctx.bus.subscribe("task/done", self._on_task_end, name="brain.task_done"),
                      ctx.bus.subscribe("task/failed", self._on_task_end, name="brain.task_failed"),
                      ctx.bus.subscribe("task/cancelled", self._on_task_end, name="brain.task_cancelled"),
                      ctx.bus.subscribe("safety/estop_state", self._on_estop, name="brain.estop")]
        skills = ctx.extras.get("skills")
        constitution = Constitution(ctx.config, ctx.safety.permissions,
                                    set(skills.names()) if skills else None)
        self.council = Council(ctx, self.planner, constitution, MentalSimulator(ctx.config),
                               cfg.confidence_threshold)
        self.tree = Selector(
            "root",
            Sequence("estop", Condition("estop engaged", lambda bb: bb["estop"]),
                     Action("hold", self._hold)),
            Sequence("charge", Condition("battery low", lambda bb: bb["need_charge"]),
                     Action("go to charger", self._go_charge)),
            Sequence("task", Condition("has task", lambda bb: bb["has_task"]),
                     Action("supervise task", self._supervise)),
            Sequence("patrol", Condition("has patrol", lambda bb: bool(self.patrol)),
                     Action("patrol", self._patrol)),
            Action("idle", self._idle),
        )
        ctx.extras["brain"] = self

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    # -- events -------------------------------------------------------------
    def _on_arrived(self, msg) -> None:
        self._arrived = True          # consumed by whichever leaf owns the goal

    async def _on_goal(self, msg) -> None:
        p = msg.payload if isinstance(msg.payload, dict) else {"goal": str(msg.payload)}
        await self._deliberate(str(p["goal"]), bool(p.get("confirmed")))

    async def _deliberate(self, goal: str, confirmed: bool = False) -> Decision:
        d = await self.council.deliberate(goal, confirmed)
        await self.ctx.bus.publish("brain/decision", d.to_dict(), source=self.name)
        if d.approved:
            await self._adopt(d)
        else:
            self.pending, self.pending_since = d, self.ctx.now
            await self.ctx.bus.publish("brain/question", {"id": d.id, "question": d.question,
                                                          "options": d.options, "goal": goal},
                                       source=self.name)
        return d

    async def _adopt(self, d: Decision) -> None:
        self.plan, self.plan_idx, self.current_goal, self.pending = d.plan, 0, None, None
        self.ctx.safety.audit.record(self.ctx.now, "brain", "plan.adopt", goal=d.goal, decision=d.id)
        await self.ctx.bus.publish("brain/plan", d.plan.to_dict(), source=self.name)
        # Execution belongs to the task runner: it owns retries, timeouts, pause/resume.
        await self.ctx.bus.publish("task/start", {"name": d.goal, "origin": "brain",
                                                  "steps": [s.__dict__ for s in d.plan.steps]}, source=self.name)

    async def _on_answer(self, msg) -> None:
        p = msg.payload or {}
        answer = str(p.get("answer", "")).strip().lower()
        d = self.pending
        if d is None or (p.get("id") is not None and p["id"] != d.id):
            await self.ctx.bus.publish("brain/answer_ignored", {"answer": answer, "reason": "no open question"},
                                       source=self.name)
            return
        self.ctx.safety.audit.record(self.ctx.now, "human", "answer", decision=d.id, answer=answer)
        if answer in ("cancel", "no", "na", "stop"):
            self.pending = None
            await self.ctx.bus.publish("brain/question_closed", {"id": d.id, "outcome": "cancelled"},
                                       source=self.name)
        elif answer in ("proceed", "yes", "ok", "go", "ha"):
            if d.hard_blocked or not d.plan.steps:
                await self.ctx.bus.publish("brain/question_closed",
                                           {"id": d.id, "outcome": "refused", "reason": "hard rule"},
                                           source=self.name)
                self.pending = None
                return
            d2 = await self.council.deliberate(d.goal, human_confirmed=True)   # re-check with confirmation
            await self.ctx.bus.publish("brain/decision", d2.to_dict(), source=self.name)
            if d2.hard_blocked or not d2.plan.steps:
                self.pending = None
                await self.ctx.bus.publish("brain/question_closed", {"id": d.id, "outcome": "refused"},
                                           source=self.name)
                return
            await self._adopt(d2)
            await self.ctx.bus.publish("brain/question_closed", {"id": d.id, "outcome": "proceed"},
                                       source=self.name)
        else:                                  # anything else is a new goal
            self.pending = None
            await self.ctx.bus.publish("brain/question_closed", {"id": d.id, "outcome": "regoal"},
                                       source=self.name)
            await self._deliberate(str(p.get("answer", "")))

    async def _on_task_end(self, msg) -> None:
        if msg.topic == "task/done":
            await self.ctx.bus.publish("brain/plan_done", {"task": msg.payload}, source=self.name)
        self.plan = None

    async def _on_estop(self, msg) -> None:
        if msg.payload["engaged"]:
            self.current_goal = None
            await self.ctx.bus.publish("nav/cancel", None, source=self.name)
            await self.ctx.bus.publish("task/pause", {"reason": "estop"}, source=self.name)
            if self.ctx.state.can(SystemState.ESTOP):
                await self.ctx.state.transition(SystemState.ESTOP, msg.payload["reason"])
        elif self.ctx.state.state == SystemState.ESTOP:
            await self.ctx.state.transition(SystemState.DIAGNOSTIC, "estop reset")
            await self.ctx.state.transition(SystemState.IDLE, "post-estop diagnostic ok")

    # -- blackboard -----------------------------------------------------------
    def _blackboard(self) -> dict:
        bus, cfg = self.ctx.bus, self.ctx.config.brain
        batt = bus.latest_payload("sensor/battery", {"level": 1.0, "charging": False})
        low = batt["level"] < cfg.battery_low
        topping_up = self.mode == "charging" and batt["level"] < cfg.battery_full
        return {"estop": self.ctx.safety.estop.engaged,
                "battery": batt,
                "need_charge": low or topping_up,
                "has_task": bool((bus.latest_payload("task/status") or {}).get("active")),
                "task": (bus.latest_payload("task/status") or {}).get("task"),
                "pose": bus.latest_payload("sensor/odometry")}

    async def _set_goal(self, x: float, y: float, name: str) -> None:
        self.current_goal = {"x": x, "y": y, "name": name}
        self._arrived = False
        await self.ctx.bus.publish("nav/goal", self.current_goal, source=self.name)

    async def _set_state(self, st: SystemState, reason: str) -> None:
        if self.ctx.state.state != st and self.ctx.state.can(st):
            await self.ctx.state.transition(st, reason)

    # -- leaves ------------------------------------------------------------------
    async def _hold(self, bb: dict) -> Status:
        self.mode = "estop"
        return Status.RUNNING

    async def _go_charge(self, bb: dict) -> Status:
        c = self.ctx.config.world.charger
        if self.mode != "charging":
            self.mode = "charging"
            self._arrived = False
            if bb["has_task"]:
                await self.ctx.bus.publish("task/pause", {"reason": "battery"}, source=self.name)
            await self._set_goal(c["x"], c["y"], "charger")
            await self._set_state(SystemState.ACTIVE, "battery low, heading to charger")
            return Status.RUNNING
        if self._arrived:
            self._arrived = False
            self.current_goal = None
        if self.current_goal is None:
            if bb["battery"]["charging"]:
                await self._set_state(SystemState.CHARGING, "docked")
            else:
                await self._set_goal(c["x"], c["y"], "charger")     # missed the pad; retry
        return Status.RUNNING

    async def _supervise(self, bb: dict) -> Status:
        """A task is active: keep the system ACTIVE and make sure it is not paused for no reason."""
        task = bb["task"] or {}
        if self.mode != "task":
            self.mode = "task"
            self.current_goal = None
            self._arrived = False
        if task.get("status") == "paused" and not bb["need_charge"] and not bb["estop"]:
            await self.ctx.bus.publish("task/resume", None, source=self.name)
        await self._set_state(SystemState.ACTIVE, f"task {task.get('name')}")
        return Status.RUNNING

    async def _patrol(self, bb: dict) -> Status:
        if self.mode != "patrol":
            self.mode = "patrol"
            self.current_goal = None
            self._arrived = False
        if self._arrived:
            self._arrived = False
            self.current_goal = None
            self.patrol_idx = (self.patrol_idx + 1) % len(self.patrol)
            if self.patrol_idx == 0:
                self.laps += 1
                await self.ctx.bus.publish("brain/lap", {"laps": self.laps}, source=self.name)
        if self.current_goal is None:
            wp = self.patrol[self.patrol_idx]
            await self._set_goal(wp["x"], wp["y"], f"waypoint_{self.patrol_idx}")
            await self._set_state(SystemState.ACTIVE, "patrol")
        return Status.RUNNING

    async def _idle(self, bb: dict) -> Status:
        self.mode = "idle"
        self.current_goal = None
        if bb["battery"]["charging"]:
            await self._set_state(SystemState.CHARGING, "idle on the charging pad")
        else:
            await self._set_state(SystemState.IDLE, "nothing to do")
        return Status.SUCCESS

    # -- tick ----------------------------------------------------------------------
    async def tick(self, dt: float) -> None:
        if not self.ctx.state.is_operational and self.ctx.state.state != SystemState.ESTOP:
            return
        if self.pending and self.ctx.now - self.pending_since > self.ctx.config.brain.question_timeout:
            await self.ctx.bus.publish("brain/question_closed", {"id": self.pending.id, "outcome": "timeout"},
                                       source=self.name)
            self.pending = None
        bb = self._blackboard()
        prev_mode = self.mode
        await self.tree.tick(bb)
        if self.mode != prev_mode:
            self.ctx.safety.audit.record(self.ctx.now, "brain", "mode", frm=prev_mode, to=self.mode)
        await self.ctx.bus.publish("brain/state", {"mode": self.mode, "goal": self.current_goal,
                                                   "patrol_idx": self.patrol_idx, "laps": self.laps,
                                                   "plan": self.plan.to_dict() if self.plan else None,
                                                   "plan_idx": self.plan_idx,
                                                   "question": self.pending.question if self.pending else None,
                                                   "question_id": self.pending.id if self.pending else None,
                                                   "active": self.tree.last_active},
                                   source=self.name)

    def describe_tree(self) -> dict:
        return self.tree.describe()
