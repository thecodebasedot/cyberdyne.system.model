"""Safety envelope: hard limits no planner can override."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..kernel.config import SafetyConfig
from ..sim.world import Obstacle


@dataclass
class Violation:
    rule: str
    detail: str


@dataclass
class SafetyEnvelope:
    cfg: SafetyConfig
    keep_out: list[Obstacle] = field(init=False)

    def __post_init__(self) -> None:
        self.keep_out = [Obstacle(**z) for z in self.cfg.keep_out]

    def clamp(self, linear: float, angular: float, *, pose: dict | None = None,
              front_clearance: float | None = None, lookahead: float = 0.5
              ) -> tuple[float, float, list[Violation]]:
        v: list[Violation] = []
        lin, ang = linear, angular
        if abs(lin) > self.cfg.max_linear:
            v.append(Violation("max_linear", f"{lin:.2f} > {self.cfg.max_linear}"))
            lin = math.copysign(self.cfg.max_linear, lin)
        if abs(ang) > self.cfg.max_angular:
            v.append(Violation("max_angular", f"{ang:.2f} > {self.cfg.max_angular}"))
            ang = math.copysign(self.cfg.max_angular, ang)
        if (front_clearance is not None and lin > 0
                and front_clearance < self.cfg.obstacle_stop_distance):
            v.append(Violation("obstacle_stop", f"clearance {front_clearance:.2f}"))
            lin = 0.0
        if pose is not None and lin != 0.0 and self.keep_out:
            nx = pose["x"] + lin * math.cos(pose["theta"]) * lookahead
            ny = pose["y"] + lin * math.sin(pose["theta"]) * lookahead
            for z in self.keep_out:
                if z.contains(nx, ny):
                    v.append(Violation("keep_out", z.name or f"zone@{z.x},{z.y}"))
                    lin = 0.0
                    break
        return lin, ang, v
