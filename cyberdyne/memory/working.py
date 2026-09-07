"""Working memory: small key/value store with TTL, the brain's scratchpad."""
from __future__ import annotations

from typing import Any


class WorkingMemory:
    def __init__(self, clock) -> None:
        self._clock = clock
        self._items: dict[str, tuple[Any, float | None]] = {}

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        exp = None if ttl is None else self._clock.now() + ttl
        self._items[key] = (value, exp)

    def get(self, key: str, default: Any = None) -> Any:
        item = self._items.get(key)
        if item is None:
            return default
        value, exp = item
        if exp is not None and self._clock.now() >= exp:
            del self._items[key]
            return default
        return value

    def pop(self, key: str, default: Any = None) -> Any:
        v = self.get(key, default)
        self._items.pop(key, None)
        return v

    def sweep(self) -> int:
        now = self._clock.now()
        dead = [k for k, (_, e) in self._items.items() if e is not None and now >= e]
        for k in dead:
            del self._items[k]
        return len(dead)

    def to_dict(self) -> dict:
        self.sweep()
        return {k: v for k, (v, _) in self._items.items()}
