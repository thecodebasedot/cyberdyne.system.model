"""System-level state machine.

Transitions are whitelisted. Anything not in the table raises, which keeps
"how did the robot end up in that state" answerable from the audit log.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from .bus import MessageBus


class SystemState(StrEnum):
    BOOT = "boot"
    DIAGNOSTIC = "diagnostic"
    IDLE = "idle"
    ACTIVE = "active"
    CHARGING = "charging"
    ESTOP = "estop"
    SHUTDOWN = "shutdown"


_TRANSITIONS: dict[SystemState, set[SystemState]] = {
    SystemState.BOOT: {SystemState.DIAGNOSTIC, SystemState.SHUTDOWN},
    SystemState.DIAGNOSTIC: {SystemState.IDLE, SystemState.ESTOP, SystemState.SHUTDOWN},
    SystemState.IDLE: {SystemState.ACTIVE, SystemState.CHARGING, SystemState.ESTOP,
                       SystemState.SHUTDOWN},
    SystemState.ACTIVE: {SystemState.IDLE, SystemState.CHARGING, SystemState.ESTOP,
                         SystemState.SHUTDOWN},
    SystemState.CHARGING: {SystemState.IDLE, SystemState.ACTIVE, SystemState.ESTOP,
                           SystemState.SHUTDOWN},
    SystemState.ESTOP: {SystemState.DIAGNOSTIC, SystemState.SHUTDOWN},
    SystemState.SHUTDOWN: set(),
}


class IllegalTransition(RuntimeError):
    pass


@dataclass
class Transition:
    frm: SystemState
    to: SystemState
    reason: str
    at: float


@dataclass
class StateMachine:
    bus: MessageBus
    state: SystemState = SystemState.BOOT
    history: list[Transition] = field(default_factory=list)
    _listeners: list[Callable[[Transition], None | Awaitable[None]]] = field(default_factory=list)

    TOPIC = "kernel/state"

    def can(self, to: SystemState) -> bool:
        return to in _TRANSITIONS[self.state]

    async def transition(self, to: SystemState, reason: str = "") -> Transition:
        if to == self.state:
            return Transition(self.state, to, reason or "noop", self.bus._clock.now())
        if not self.can(to):
            raise IllegalTransition(f"{self.state.value} -> {to.value} ({reason})")
        tr = Transition(self.state, to, reason, self.bus._clock.now())
        self.state = to
        self.history.append(tr)
        await self.bus.publish(self.TOPIC, {"from": tr.frm.value, "to": tr.to.value,
                                            "reason": reason}, source="state")
        return tr

    @property
    def is_operational(self) -> bool:
        return self.state in (SystemState.IDLE, SystemState.ACTIVE, SystemState.CHARGING)
