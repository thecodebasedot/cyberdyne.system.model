"""Constitution: hard rules every plan must satisfy before dispatch.

The LLM (or any planner) proposes; the constitution disposes. These checks
are deliberately dumb and deterministic so they can be audited, tested and
eventually formally verified. Nothing here consults a model.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..kernel.config import RobotConfig
from ..safety.permissions import PermissionPolicy, Tier
from ..sim.world import Obstacle
from .planner import Plan


@dataclass(frozen=True)
class RuleViolation:
    rule: str
    step: int | None
    detail: str

    def to_dict(self) -> dict:
        return {"rule": self.rule, "step": self.step, "detail": self.detail}


class Constitution:
    max_steps = 12

    def __init__(self, config: RobotConfig, permissions: PermissionPolicy,
                 known_skills: set[str] | None = None) -> None:
        self.config = config
        self.permissions = permissions
        self.known_skills = known_skills
        self.keep_out = [Obstacle(**z) for z in config.safety.keep_out]

    def review(self, plan: Plan, human_confirmed: bool = False) -> list[RuleViolation]:
        v: list[RuleViolation] = []
        w = self.config.world
        if len(plan.steps) > self.max_steps:
            v.append(RuleViolation("max_steps", None, f"{len(plan.steps)} > {self.max_steps}"))
        for i, step in enumerate(plan.steps):
            if step.skill == "task":
                continue                                   # library task: validated when its steps run
            if self.known_skills is not None and step.skill not in self.known_skills:
                v.append(RuleViolation("unknown_skill", i, step.skill))
                continue
            action = f"skill.{step.skill}"
            tier = self.permissions.tier(action)
            if tier == Tier.FORBIDDEN:
                v.append(RuleViolation("forbidden_action", i, action))
            elif tier == Tier.CONFIRM and not human_confirmed:
                v.append(RuleViolation("needs_confirmation", i, action))
            if step.skill == "goto":
                try:
                    x, y = float(step.args["x"]), float(step.args["y"])
                except (KeyError, TypeError, ValueError):
                    v.append(RuleViolation("malformed_goto", i, str(step.args)))
                    continue
                if not (0 <= x <= w.width and 0 <= y <= w.height):
                    v.append(RuleViolation("out_of_bounds", i, f"({x}, {y})"))
                for z in self.keep_out:
                    if z.contains(x, y, margin=0.25):
                        v.append(RuleViolation("keep_out", i, z.name or f"zone@{z.x},{z.y}"))
                for o in w.obstacles:
                    if Obstacle(**o).contains(x, y, margin=0.2):
                        v.append(RuleViolation("inside_obstacle", i, o.get("name", "?")))
        return v

    @staticmethod
    def hard(violations: list[RuleViolation]) -> list[RuleViolation]:
        """Violations a human may NOT override."""
        return [x for x in violations if x.rule in ("forbidden_action", "keep_out", "out_of_bounds",
                                                    "inside_obstacle", "unknown_skill", "malformed_goto")]
