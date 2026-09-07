"""Watchdog: detects stalled or faulted modules and restarts them.

Runs at high priority. A module is *stale* if it hasn't ticked in
``stale_factor`` periods. A module in FAULT is restarted after ``cooldown``
seconds, up to ``max_restarts`` times; beyond that it stays down and, if
critical, the e-stop is engaged.
"""
from __future__ import annotations

from .context import Context
from .module import Module, ModuleState


class Watchdog(Module):
    name = "watchdog"
    rate_hz = 2.0
    priority = 1

    def __init__(self, stale_factor: float = 5.0, cooldown: float = 1.0,
                 max_restarts: int = 3) -> None:
        super().__init__()
        self.stale_factor = stale_factor
        self.cooldown = cooldown
        self.max_restarts = max_restarts
        self._scheduler = None
        self._fault_since: dict[str, float] = {}

    def attach(self, scheduler) -> None:
        self._scheduler = scheduler

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx

    async def tick(self, dt: float) -> None:
        if self._scheduler is None:
            return
        now = self.ctx.now
        for m in self._scheduler.modules:
            if m is self:
                continue
            if m.state == ModuleState.FAULT:
                await self._maybe_restart(m, now)
            elif m.state == ModuleState.RUNNING and m.stats.last_tick_at >= 0:
                if now - m.stats.last_tick_at > m.period * self.stale_factor:
                    m.state = ModuleState.FAULT
                    await self.ctx.bus.publish("kernel/module_stale",
                                               {"module": m.name,
                                                "since": m.stats.last_tick_at},
                                               source=self.name)

    async def _maybe_restart(self, m: Module, now: float) -> None:
        since = self._fault_since.setdefault(m.name, now)
        if now - since < self.cooldown:
            return
        if m.stats.restarts >= self.max_restarts:
            if m.critical and not self.ctx.safety.estop.engaged:
                await self.ctx.safety.estop.engage(f"{m.name} exceeded restart budget")
            return
        try:
            await m.teardown()
        except Exception:  # noqa: BLE001
            self.log.exception("teardown during restart failed for %s", m.name)
        await self._scheduler.setup_module(m)
        m.stats.restarts += 1
        self._fault_since.pop(m.name, None)
        self.ctx.safety.audit.record(now, "watchdog", "module.restart", module=m.name,
                                     restarts=m.stats.restarts, ok=m.state == ModuleState.READY)
        await self.ctx.bus.publish("kernel/module_restart",
                                   {"module": m.name, "restarts": m.stats.restarts,
                                    "ok": m.state == ModuleState.READY},
                                   source=self.name)
