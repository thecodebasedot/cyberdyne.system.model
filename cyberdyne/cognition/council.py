"""The council: several narrow roles deliberate before the brain commits.

    Perceiver      -> situation brief (what is true right now)
    Planner        -> candidate plan (rule-based or LLM)
    SafetyOfficer  -> constitution review (hard rules, permissions)
    Critic         -> mental simulation + heuristics -> confidence
    Council        -> Decision: approve / ask the human / reject

The council never actuates anything; the brain executes approved decisions.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..kernel.context import Context
from .constitution import Constitution, RuleViolation
from .planner import Plan, Planner
from .simulate import MentalSimulator, Rollout

_ids = itertools.count(1)


@dataclass
class Decision:
    id: int
    goal: str
    plan: Plan
    brief: dict
    violations: list[RuleViolation]
    rollout: Rollout | None
    confidence: float
    approved: bool
    question: str | None = None
    options: list[str] = field(default_factory=list)
    critique: list[str] = field(default_factory=list)

    @property
    def hard_blocked(self) -> bool:
        return bool(Constitution.hard(self.violations))

    def to_dict(self) -> dict:
        return {"id": self.id, "goal": self.goal, "plan": self.plan.to_dict(), "brief": self.brief,
                "violations": [v.to_dict() for v in self.violations],
                "rollout": self.rollout.to_dict() if self.rollout else None,
                "confidence": round(self.confidence, 3), "approved": self.approved,
                "question": self.question, "options": self.options, "critique": self.critique}


class Perceiver:
    """Compresses the bus into a situation brief the planner and critic share."""

    def brief(self, ctx: Context, skills: list[dict]) -> dict:
        bus = ctx.bus
        wm = ctx.extras.get("world_model")
        w = ctx.config.world
        return {
            "time": round(ctx.now, 1),
            "state": ctx.state.state.value,
            "pose": bus.latest_payload("sensor/odometry"),
            "battery": bus.latest_payload("sensor/battery"),
            "estop": ctx.safety.estop.engaged,
            "front_clearance": bus.latest_payload("perception/front_clearance"),
            "world": {"width": w.width, "height": w.height, "charger": w.charger,
                      "keep_out": ctx.config.safety.keep_out},
            "known_entities": wm.entities.all() if wm else [],
            "places": wm.places() if wm else {},
            "room": wm.current_room if wm else None,
            "explored": round(wm.grid.explored_fraction(), 2) if wm else None,
            "patrol": ctx.config.brain.patrol,
            "skills": [{"name": s["name"], "args": s["args"], "description": s["description"]}
                       for s in skills],
        }


class SafetyOfficer:
    def __init__(self, constitution: Constitution) -> None:
        self.constitution = constitution

    def review(self, plan: Plan, human_confirmed: bool) -> list[RuleViolation]:
        return self.constitution.review(plan, human_confirmed)


class Critic:
    def __init__(self, simulator: MentalSimulator, battery_reserve: float = 0.15) -> None:
        self.simulator = simulator
        self.battery_reserve = battery_reserve

    def assess(self, plan: Plan, ctx: Context, brief: dict) -> tuple[float, Rollout | None, list[str]]:
        notes: list[str] = []
        if not plan.steps:
            return 0.0, None, ["plan is empty"]
        conf = 1.0
        if plan.rationale.startswith("[rule fallback"):
            conf *= 0.8
            notes.append("planner fell back to rules")
        wm = ctx.extras.get("world_model")
        pose = brief.get("pose")
        rollout = None
        if wm and pose and any(s.skill == "goto" for s in plan.steps):
            batt = (brief.get("battery") or {}).get("level", 1.0)
            rollout = self.simulator.rollout(plan, wm.grid, pose, batt)
            if not rollout.feasible:
                conf *= 0.3
                notes.append("mental simulation could not reach every goal")
            if rollout.battery_after < self.battery_reserve:
                conf *= 0.5
                notes.append(f"predicted battery {rollout.battery_after:.0%} below reserve")
            if (brief.get("explored") or 0) < 0.3:
                conf *= 0.85
                notes.append("map mostly unexplored; prediction is weak")
        if len(plan.steps) > 6:
            conf *= 0.9
            notes.append("long plan")
        return conf, rollout, notes


class Council:
    def __init__(self, ctx: Context, planner: Planner, constitution: Constitution,
                 simulator: MentalSimulator, confidence_threshold: float = 0.6) -> None:
        self.ctx = ctx
        self.perceiver = Perceiver()
        self.planner = planner
        self.safety = SafetyOfficer(constitution)
        self.critic = Critic(simulator)
        self.threshold = confidence_threshold
        self.history: list[Decision] = []

    async def deliberate(self, goal: str, human_confirmed: bool = False) -> Decision:
        skills = self.ctx.extras["skills"].describe() if "skills" in self.ctx.extras else []
        brief = self.perceiver.brief(self.ctx, skills)
        plan = await self.planner.plan(goal, brief)
        violations = self.safety.review(plan, human_confirmed)
        confidence, rollout, critique = self.critic.assess(plan, self.ctx, brief)
        hard = Constitution.hard(violations)
        approved = not violations and confidence >= self.threshold and bool(plan.steps)
        question, options = None, []
        if hard:
            question = f"I can't do '{goal}': " + "; ".join(f"{v.rule} ({v.detail})" for v in hard) + \
                       ". Give me a different goal or say cancel."
            options = ["cancel"]
        elif violations:
            question = f"'{goal}' needs your confirmation: " + \
                       "; ".join(f"{v.rule} ({v.detail})" for v in violations) + ". Proceed?"
            options = ["proceed", "cancel"]
        elif not plan.steps:
            question = f"I don't know how to do '{goal}'. {plan.rationale}".strip()
            options = ["cancel"]
        elif not approved:
            question = f"I'm only {confidence:.0%} confident about '{goal}' " + \
                       f"({'; '.join(critique)}). Proceed anyway?"
            options = ["proceed", "cancel"]
        d = Decision(next(_ids), goal, plan, brief, violations, rollout, confidence, approved,
                     question, options, critique)
        self.history.append(d)
        self.ctx.safety.audit.record(self.ctx.now, "council", "decision", id=d.id, goal=goal,
                                     approved=approved, confidence=round(confidence, 2),
                                     violations=[v.rule for v in violations])
        return d
