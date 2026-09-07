from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Experience:
    ts: float
    observation: dict[str, Any]
    action: dict[str, Any]
    reward: float = 0.0
    info: dict[str, Any] = field(default_factory=dict)


class ReplayBuffer:
    def __init__(self, capacity: int = 10_000) -> None:
        self._buf: deque[Experience] = deque(maxlen=capacity)

    def add(self, e: Experience) -> None:
        self._buf.append(e)

    def __len__(self) -> int:
        return len(self._buf)

    def sample(self, n: int) -> list[Experience]:
        return list(self._buf)[-n:]


class Learner(ABC):
    """A learner consumes experience and proposes parameter updates.

    Updates never go straight to actuators: they pass through the safety
    core's permission policy (``self.modify`` is FORBIDDEN by default).
    """

    @abstractmethod
    async def observe(self, e: Experience) -> None: ...

    @abstractmethod
    async def propose(self) -> dict[str, Any]: ...
