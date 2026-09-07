"""Clock abstraction.

Every module reads time through the kernel clock, never through ``time.time``.
That single rule is what makes deterministic simulation and replay possible:
swap ``WallClock`` for ``SimClock`` and the whole robot runs as fast as the
CPU allows (or at any real-time factor) with bit-identical behaviour.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod


class Clock(ABC):
    @abstractmethod
    def now(self) -> float:
        """Seconds since kernel epoch."""

    @abstractmethod
    async def sleep_until(self, t: float) -> None:
        """Suspend the caller until ``now() >= t``."""

    async def sleep(self, dt: float) -> None:
        await self.sleep_until(self.now() + dt)


class WallClock(Clock):
    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep_until(self, t: float) -> None:
        delay = t - self.now()
        await asyncio.sleep(delay if delay > 0 else 0)


class SimClock(Clock):
    """Discrete-event clock.

    ``sleep_until`` jumps straight to the requested time. With
    ``realtime_factor`` set (e.g. 1.0) it additionally waits on the wall
    clock so a human can watch the simulation at a sensible speed.
    """

    def __init__(self, start: float = 0.0, realtime_factor: float | None = None) -> None:
        self._t = start
        self.realtime_factor = realtime_factor

    def now(self) -> float:
        return self._t

    async def sleep_until(self, t: float) -> None:
        if t > self._t:
            if self.realtime_factor:
                await asyncio.sleep((t - self._t) / self.realtime_factor)
            self._t = t
        # Always yield so other coroutines (dashboard bridge, tests) get a turn.
        await asyncio.sleep(0)

    def advance(self, dt: float) -> None:
        self._t += dt
