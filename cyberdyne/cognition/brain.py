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
from .planner import Plan, Planner, RulePlanner


class Brain(Module):
    name = "brain"
    rate_hz = 2.0
    priority = 50

    def __init__(self, planner: Planner | None = None) -> None:
        super().__init__()
        self.planner = planner or RulePlanner()

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
                      ctx.bus.subscribe("safety/estop_state", self._on_estop, name="brain.estop")]
        self.tree = Selector(
            "root",
            Sequence("estop", Condition("estop engaged", lambda bb: bb["estop"]),
                     Action("hold", self._hold)),
            Sequence("charge", Condition("battery low", lambda bb: bb["need_charge"]),
                     Action("go to charger", self._go_charge)),
            Sequence("plan", Condition("has plan", lambda bb: bb["has_plan"]),
                     Action("execute plan", self._run_plan)),
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
        goal = msg.payload["goal"] if isinstance(msg.payload, dict) else str(msg.payload)
        ctx = {"charger": self.ctx.config.world.charger, "patrol": self.patrol}
        self.plan = await self.planner.plan(goal, ctx)
        self.plan_idx = 0
        self.current_goal = None
        await self.ctx.bus.publish("brain/plan", self.plan.to_dict(), source=self.name)

    async def _on_estop(self, msg) -> None:
        if msg.payload["engaged"]:
            self.current_goal = None
            await self.ctx.bus.publish("nav/cancel", None, source=self.name)
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
                "has_plan": self.plan is not None and self.plan_idx < len(self.plan.steps),
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

    async def _run_plan(self, bb: dict) -> Status:
        step = self.plan.steps[self.plan_idx]
        if self.mode != "plan":
            self.mode = "plan"
            self.current_goal = None
            self._arrived = False
        if self._arrived:
            self._arrived = False
            self.plan_idx += 1
            self.current_goal = None
            if self.plan_idx >= len(self.plan.steps):
                await self.ctx.bus.publish("brain/plan_done", self.plan.to_dict(), source=self.name)
                self.plan = None
                self.mode = "idle"
                return Status.SUCCESS
            step = self.plan.steps[self.plan_idx]
        if self.current_goal is None:
            if step.skill == "goto":
                await self._set_goal(step.args["x"], step.args["y"], step.args.get("name", "plan"))
                await self._set_state(SystemState.ACTIVE, f"plan step {self.plan_idx}: {step.skill}")
            else:
                await self.ctx.bus.publish("skill/invoke", {"skill": step.skill, "args": step.args},
                                           source=self.name)
                self.plan_idx += 1
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
        bb = self._blackboard()
        prev_mode = self.mode
        await self.tree.tick(bb)
        if self.mode != prev_mode:
            self.ctx.safety.audit.record(self.ctx.now, "brain", "mode", frm=prev_mode, to=self.mode)
        await self.ctx.bus.publish("brain/state", {"mode": self.mode, "goal": self.current_goal,
                                                   "patrol_idx": self.patrol_idx, "laps": self.laps,
                                                   "plan": self.plan.to_dict() if self.plan else None,
                                                   "plan_idx": self.plan_idx,
                                                   "active": self.tree.last_active},
                                   source=self.name)

    def describe_tree(self) -> dict:
        return self.tree.describe()
