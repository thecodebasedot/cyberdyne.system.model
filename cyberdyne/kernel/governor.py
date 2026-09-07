"""Resource governor: per-module CPU budgets with automatic throttling.

Every module gets a budget = ``budget_fraction`` of its period. If its tick
time exceeds the budget for ``window`` consecutive ticks, the governor
halves its rate (down to ``min_rate``) and publishes ``kernel/throttled``;
when it stays under budget for a while the rate is restored step by step.
Safety-critical modules (priority < 10) are never throttled.
"""
from __future__ import annotations

from .context import Context
from .module import Module, ModuleState


class Governor(Module):
    name = "governor"
    rate_hz = 2.0
    priority = 3

    def __init__(self, budget_fraction: float = 0.5, window: int = 5, min_rate: float = 1.0,
                 recover_after: float = 5.0) -> None:
        super().__init__()
        self.budget_fraction, self.window = budget_fraction, window
        self.min_rate, self.recover_after = min_rate, recover_after
        self._scheduler = None
        self._over: dict[str, int] = {}
        self._nominal: dict[str, float] = {}
        self._since_ok: dict[str, float] = {}
        self.throttles = 0

    def attach(self, scheduler) -> None:
        self._scheduler = scheduler

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx

    async def tick(self, dt: float) -> None:
        if self._scheduler is None:
            return
        now = self.ctx.now
        for m in self._scheduler.modules:
            if m is self or m.priority < 10 or m.state not in (ModuleState.READY, ModuleState.RUNNING):
                continue
            self._nominal.setdefault(m.name, m.rate_hz)
            budget = self.budget_fraction / max(m.rate_hz, 1e-6)
            if m.stats.last_tick_duration > budget:
                self._over[m.name] = self._over.get(m.name, 0) + 1
                self._since_ok[m.name] = now
            else:
                self._over[m.name] = 0
            if self._over.get(m.name, 0) >= self.window and m.rate_hz > self.min_rate:
                m.rate_hz = max(self.min_rate, m.rate_hz / 2)
                self._over[m.name] = 0
                self.throttles += 1
                self.ctx.safety.audit.record(now, "governor", "throttle", module=m.name, rate_hz=m.rate_hz,
                                             tick_s=round(m.stats.last_tick_duration, 4))
                await self.ctx.bus.publish("kernel/throttled", {"module": m.name, "rate_hz": m.rate_hz,
                                                                "nominal_hz": self._nominal[m.name]}, source=self.name)
            elif (m.rate_hz < self._nominal[m.name] and now - self._since_ok.get(m.name, -1e9) > self.recover_after):
                m.rate_hz = min(self._nominal[m.name], m.rate_hz * 2)
                self._since_ok[m.name] = now
                await self.ctx.bus.publish("kernel/unthrottled", {"module": m.name, "rate_hz": m.rate_hz},
                                           source=self.name)

    def status(self) -> dict:
        mods = self._scheduler.modules if self._scheduler else []
        return {"throttles": self.throttles,
                "throttled": {m.name: m.rate_hz for m in mods
                              if m.name in self._nominal and m.rate_hz < self._nominal[m.name]}}
