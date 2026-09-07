"""The Context is the single object handed to every module on setup.

It is deliberately a plain container: modules talk to each other through the
bus, never by grabbing another module out of the context.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .bus import MessageBus
from .clock import Clock

if TYPE_CHECKING:  # pragma: no cover
    from ..hal.registry import DeviceRegistry
    from ..safety.core import SafetyCore
    from ..sim.world import World
    from .config import RobotConfig
    from .state import StateMachine


@dataclass
class Context:
    clock: Clock
    bus: MessageBus
    config: RobotConfig
    state: StateMachine
    devices: DeviceRegistry
    safety: SafetyCore
    world: World | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def logger(self, name: str) -> logging.Logger:
        return logging.getLogger(f"cyberdyne.{name}")

    @property
    def now(self) -> float:
        return self.clock.now()
