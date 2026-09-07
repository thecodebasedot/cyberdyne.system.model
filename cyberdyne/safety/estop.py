from __future__ import annotations

from collections.abc import Awaitable, Callable


class EStop:
    """Emergency stop latch. Engaging is instant; reset requires an explicit call."""

    def __init__(self) -> None:
        self.engaged = False
        self.reason = ""
        self.count = 0
        self._on_engage: list[Callable[[str], Awaitable[None] | None]] = []
        self._on_reset: list[Callable[[], Awaitable[None] | None]] = []

    def on_engage(self, cb) -> None:
        self._on_engage.append(cb)

    def on_reset(self, cb) -> None:
        self._on_reset.append(cb)

    async def engage(self, reason: str) -> None:
        if self.engaged:
            return
        self.engaged = True
        self.reason = reason
        self.count += 1
        for cb in self._on_engage:
            r = cb(reason)
            if hasattr(r, "__await__"):
                await r

    async def reset(self) -> None:
        if not self.engaged:
            return
        self.engaged = False
        self.reason = ""
        for cb in self._on_reset:
            r = cb()
            if hasattr(r, "__await__"):
                await r
