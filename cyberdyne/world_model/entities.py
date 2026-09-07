"""Entity store: things the robot knows about (places, objects, people)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entity:
    id: str
    kind: str
    x: float
    y: float
    attrs: dict = field(default_factory=dict)
    first_seen: float = 0.0
    last_seen: float = 0.0
    seen_count: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class EntityStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Entity] = {}

    def observe(self, id: str, kind: str, x: float, y: float, ts: float, **attrs) -> Entity:
        e = self._by_id.get(id)
        if e is None:
            e = Entity(id, kind, x, y, dict(attrs), ts, ts, 1)
            self._by_id[id] = e
        else:
            e.x, e.y, e.last_seen = x, y, ts
            e.seen_count += 1
            e.attrs.update(attrs)
        return e

    def get(self, id: str) -> Entity | None:
        return self._by_id.get(id)

    def by_kind(self, kind: str) -> list[Entity]:
        return [e for e in self._by_id.values() if e.kind == kind]

    def nearest(self, x: float, y: float, kind: str | None = None) -> Entity | None:
        cands = self.by_kind(kind) if kind else list(self._by_id.values())
        return min(cands, key=lambda e: (e.x - x) ** 2 + (e.y - y) ** 2, default=None)

    def all(self) -> list[dict]:
        return [e.to_dict() for e in self._by_id.values()]
