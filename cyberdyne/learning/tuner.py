"""ControllerTuner: shielded parameter learning for the local controller.

Offline, in simulation: a (1+1) evolution strategy over the controller's
parameters, scored by how fast the robot patrols, how often it touches
walls and how many recoveries it needs. The safety gate is never part of
the search space; whatever the tuner proposes still passes through it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..kernel.config import RobotConfig

PARAM_BOUNDS = {                       # name: (min, max)
    "clear_needed": (0.5, 2.5),
    "lookahead": (0.3, 1.5),
    "replan_interval": (0.5, 5.0),
}
DEFAULTS = {"clear_needed": 1.2, "lookahead": 0.7, "replan_interval": 2.0}


@dataclass
class TuneResult:
    best: dict[str, float]
    best_score: float
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"best": self.best, "best_score": round(self.best_score, 3), "episodes": len(self.history)}


class ControllerTuner:
    def __init__(self, config: RobotConfig, episode_seconds: float = 90.0, seed: int = 0) -> None:
        self.config = config
        self.episode_seconds = episode_seconds
        self.rng = random.Random(seed)

    async def episode(self, params: dict[str, float]) -> tuple[float, dict]:
        from ..runtime import Runtime  # local import: runtime imports learning
        rt = Runtime(self.config)
        ctrl = rt.scheduler.get("motion")
        for k, v in params.items():
            setattr(ctrl, k, v)
        recoveries = 0
        rt.bus.subscribe("nav/recovery", lambda m: None)
        counter = {"rec": 0}
        rt.bus.subscribe("nav/recovery", lambda m: counter.__setitem__("rec", counter["rec"] + 1))
        await rt.boot()
        await rt.run(self.episode_seconds)
        await rt.shutdown()
        r = rt.report()
        recoveries = counter["rec"]
        laps = r["brain"]["laps"] if r["brain"] else 0
        dist = r["world"]["distance"]
        contacts = r["world"]["contacts"]
        collisions = r["world"]["collisions"]
        score = 10.0 * laps + 0.2 * dist - 0.02 * contacts - 2.0 * recoveries - 5.0 * collisions
        return score, {"laps": laps, "distance": round(dist, 1), "contacts": contacts, "recoveries": recoveries,
                       "collisions": collisions}

    def mutate(self, params: dict[str, float], sigma: float = 0.25) -> dict[str, float]:
        out = {}
        for k, v in params.items():
            lo, hi = PARAM_BOUNDS[k]
            out[k] = round(min(hi, max(lo, v + self.rng.gauss(0, sigma * (hi - lo)))), 3)
        return out

    async def tune(self, episodes: int = 6, start: dict[str, float] | None = None) -> TuneResult:
        best = dict(start or DEFAULTS)
        best_score, stats = await self.episode(best)
        result = TuneResult(best, best_score, [{"params": best, "score": round(best_score, 2), **stats}])
        for _ in range(max(0, episodes - 1)):
            cand = self.mutate(best)
            score, stats = await self.episode(cand)
            result.history.append({"params": cand, "score": round(score, 2), **stats})
            if score > best_score:
                best, best_score = cand, score
        result.best, result.best_score = best, best_score
        return result
