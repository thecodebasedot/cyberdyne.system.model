"""Module lifecycle.

A Module is the unit of scheduling, isolation and restart. It declares how
often it wants to run (``rate_hz``) and how important it is (``priority``,
lower runs first inside a frame). The scheduler owns the loop; a module only
ever implements ``setup``, ``tick`` and ``teardown``.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from .context import Context


class ModuleState(StrEnum):
    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    FAULT = "fault"
    STOPPED = "stopped"


@dataclass
class ModuleStats:
    ticks: int = 0
    faults: int = 0
    restarts: int = 0
    deadline_misses: int = 0
    last_tick_at: float = -1.0
    last_tick_duration: float = 0.0
    max_tick_duration: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Module(ABC):
    name: str = "module"
    rate_hz: float = 10.0
    priority: int = 50        # 0 = safety-critical, 100 = background
    max_faults: int = 3       # consecutive tick failures before FAULT
    critical: bool = False    # if True, a FAULT triggers an e-stop

    def __init__(self) -> None:
        self.ctx: Context | None = None
        self.state = ModuleState.CREATED
        self.stats = ModuleStats()
        self.next_due = 0.0
        self._consecutive_faults = 0
        self.log = logging.getLogger(f"cyberdyne.{self.name}")

    # -- lifecycle (override) ------------------------------------------------
    async def setup(self, ctx: Context) -> None:  # noqa: B027
        """Acquire devices, subscribe to topics."""

    @abstractmethod
    async def tick(self, dt: float) -> None:
        """One scheduled iteration. ``dt`` is seconds since the previous tick."""

    async def teardown(self) -> None:  # noqa: B027
        """Release resources. Must be idempotent."""

    # -- helpers used by the scheduler/watchdog --------------------------------
    @property
    def period(self) -> float:
        return 1.0 / self.rate_hz

    def _record_success(self) -> None:
        self._consecutive_faults = 0

    def _record_fault(self) -> bool:
        """Returns True if the module should be moved to FAULT."""
        self._consecutive_faults += 1
        self.stats.faults += 1
        return self._consecutive_faults >= self.max_faults

    def describe(self) -> dict:
        return {"name": self.name, "state": self.state.value, "rate_hz": self.rate_hz,
                "priority": self.priority, "critical": self.critical,
                "stats": self.stats.to_dict()}
