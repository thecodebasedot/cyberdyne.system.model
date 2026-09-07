"""Frame-based cooperative scheduler.

Each frame: every module whose ``next_due`` has arrived runs once, in
priority order, then the scheduler sleeps until the earliest ``next_due``.
Because there is exactly one loop and modules run in a fixed order, a
simulation is fully deterministic given the same inputs.

Fault policy:
  * a tick that raises increments the module's fault counter and is reported
    on ``kernel/fault``; after ``max_faults`` consecutive failures the module
    is parked in FAULT (the watchdog decides whether to restart it);
  * a FAULT on a ``critical`` module engages the e-stop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .bus import MessageBus
from .clock import Clock
from .context import Context
from .module import Module, ModuleState

log = logging.getLogger("cyberdyne.scheduler")


@dataclass
class SchedulerStats:
    frames: int = 0
    ticks: int = 0
    faults: int = 0
    started_at: float = 0.0
    stopped_at: float = 0.0
    per_module: dict[str, dict] = field(default_factory=dict)


class Scheduler:
    def __init__(self, clock: Clock, bus: MessageBus, ctx: Context) -> None:
        self.clock = clock
        self.bus = bus
        self.ctx = ctx
        self.modules: list[Module] = []
        self.stats = SchedulerStats()
        self._stop = asyncio.Event()
        self.running = False

    # -- registration -------------------------------------------------------
    def register(self, *modules: Module) -> None:
        for m in modules:
            if any(x.name == m.name for x in self.modules):
                raise ValueError(f"duplicate module name: {m.name}")
            self.modules.append(m)
        self.modules.sort(key=lambda m: m.priority)

    def get(self, name: str) -> Module:
        for m in self.modules:
            if m.name == name:
                return m
        raise KeyError(name)

    # -- lifecycle ------------------------------------------------------------
    async def setup_module(self, m: Module) -> None:
        try:
            m.ctx = self.ctx
            await m.setup(self.ctx)
            m.state = ModuleState.READY
            m.next_due = self.clock.now()
            m._consecutive_faults = 0
        except Exception as exc:  # noqa: BLE001
            m.state = ModuleState.FAULT
            log.exception("setup failed for %s", m.name)
            await self._report_fault(m, "setup", exc)

    async def setup_all(self) -> None:
        for m in self.modules:
            await self.setup_module(m)

    async def teardown_all(self) -> None:
        for m in reversed(self.modules):
            try:
                await m.teardown()
            except Exception:  # noqa: BLE001
                log.exception("teardown failed for %s", m.name)
            m.state = ModuleState.STOPPED

    def stop(self) -> None:
        self._stop.set()

    async def run(self, duration: float | None = None) -> SchedulerStats:
        self.running = True
        self._stop.clear()
        self.stats.started_at = self.clock.now()
        deadline = None if duration is None else self.clock.now() + duration
        try:
            while not self._stop.is_set():
                now = self.clock.now()
                if deadline is not None and now >= deadline:
                    break
                await self._frame(now)
                nxt = min((m.next_due for m in self.modules if m.state in
                           (ModuleState.READY, ModuleState.RUNNING)), default=now + 0.1)
                if deadline is not None:
                    nxt = min(nxt, deadline)
                await self.clock.sleep_until(nxt)
        finally:
            self.running = False
            self.stats.stopped_at = self.clock.now()
            self.stats.per_module = {m.name: m.describe() for m in self.modules}
        return self.stats

    # -- frame ---------------------------------------------------------------
    async def frame(self, now: float) -> None:
        """Run every due module once (used by lock-step harnesses such as FleetSim)."""
        await self._frame(now)

    def next_due(self, default: float) -> float:
        live = [m.next_due for m in self.modules if m.state in (ModuleState.READY, ModuleState.RUNNING)]
        return min(live) if live else default

    async def _frame(self, now: float) -> None:
        self.stats.frames += 1
        for m in self.modules:
            if m.state not in (ModuleState.READY, ModuleState.RUNNING) or now < m.next_due:
                continue
            dt = m.period if m.stats.last_tick_at < 0 else now - m.stats.last_tick_at
            m.state = ModuleState.RUNNING
            wall0 = time.perf_counter()
            try:
                await m.tick(dt)
                m._record_success()
            except Exception as exc:  # noqa: BLE001
                self.stats.faults += 1
                if m._consecutive_faults == 0:
                    log.exception("tick failed for %s", m.name)
                else:
                    log.warning("tick failed again for %s: %r", m.name, exc)
                await self._report_fault(m, "tick", exc)
                if m._record_fault():
                    m.state = ModuleState.FAULT
                    self.ctx.safety.audit.record(now, "scheduler", "module.fault", module=m.name,
                                                 error=repr(exc), critical=m.critical)
                    await self.bus.publish("kernel/module_fault",
                                           {"module": m.name, "critical": m.critical},
                                           source="scheduler")
                    if m.critical:
                        await self.ctx.safety.estop.engage(f"critical module {m.name} faulted")
                    continue
            wall = time.perf_counter() - wall0
            m.stats.ticks += 1
            self.stats.ticks += 1
            m.stats.last_tick_at = now
            m.stats.last_tick_duration = wall
            m.stats.max_tick_duration = max(m.stats.max_tick_duration, wall)
            if wall > m.period:
                m.stats.deadline_misses += 1
            # schedule next; if we fell behind, skip missed slots instead of bursting
            m.next_due += m.period
            if m.next_due <= now:
                m.next_due = now + m.period

    async def _report_fault(self, m: Module, phase: str, exc: BaseException) -> None:
        await self.bus.publish(self.bus.FAULT_TOPIC,
                               {"kind": phase, "module": m.name, "error": repr(exc)},
                               source="scheduler")

    def describe(self) -> dict:
        return {"running": self.running, "frames": self.stats.frames, "ticks": self.stats.ticks,
                "faults": self.stats.faults,
                "modules": [m.describe() for m in self.modules]}
