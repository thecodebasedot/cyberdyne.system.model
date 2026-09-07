"""Homeostatic drives: internal state that shapes behaviour when nothing
external demands attention.

    energy     = battery level                      (low -> charge; already handled by the brain)
    curiosity  = unexplored fraction of the map     (high -> explore a frontier when idle)
    social     = time since anyone interacted       (high -> greet / suggest more readily)
    safety     = recent alerts and faults           (high -> patrol instead of exploring)

Published on ``brain/drives``; the brain reads ``dominant``.
"""
from __future__ import annotations

import math

from ..kernel.context import Context
from ..kernel.module import Module


class DrivesModule(Module):
    name = "drives"
    rate_hz = 1.0
    priority = 48

    def __init__(self, social_half_life: float = 300.0) -> None:
        super().__init__()
        self.social_half_life = social_half_life
        self.last_interaction = 0.0
        self.recent_alerts = 0.0
        self.values = {"energy": 1.0, "curiosity": 0.0, "social": 0.0, "safety": 0.0}

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._subs = [ctx.bus.subscribe("language/utterance", self._touch, name="drives.utt"),
                      ctx.bus.subscribe("social/recognised", self._touch, name="drives.seen"),
                      ctx.bus.subscribe("security/alert", self._alert, name="drives.alert"),
                      ctx.bus.subscribe("kernel/module_fault", self._alert, name="drives.fault")]
        ctx.extras["drives"] = self

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    def _touch(self, msg) -> None:
        self.last_interaction = msg.ts

    def _alert(self, msg) -> None:
        self.recent_alerts = min(1.0, self.recent_alerts + 0.5)

    async def tick(self, dt: float) -> None:
        bus, now = self.ctx.bus, self.ctx.now
        wm = self.ctx.extras.get("world_model")
        batt = (bus.latest_payload("sensor/battery") or {}).get("level", 1.0)
        self.recent_alerts *= math.exp(-dt * math.log(2) / 120.0)
        self.values = {
            "energy": round(batt, 3),
            "curiosity": round(1.0 - (wm.grid.explored_fraction() if wm else 1.0), 3),
            "social": round(1.0 - math.exp(-(now - self.last_interaction) * math.log(2) / self.social_half_life), 3),
            "safety": round(self.recent_alerts, 3),
        }
        pressures = {"energy": 1.0 - batt, "curiosity": self.values["curiosity"], "social": self.values["social"],
                     "safety": self.values["safety"]}
        dominant = max(pressures.items(), key=lambda kv: kv[1])[0]
        await bus.publish("brain/drives", {**self.values, "dominant": dominant}, source=self.name)
