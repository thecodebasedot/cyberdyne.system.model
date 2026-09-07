from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FleetMessage:
    robot: str
    topic: str
    payload: Any
    ts: float


class Transport(ABC):
    @abstractmethod
    async def send(self, msg: FleetMessage) -> None: ...

    @abstractmethod
    async def receive(self) -> list[FleetMessage]: ...
