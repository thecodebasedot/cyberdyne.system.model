"""Hash-chained audit log.

Each entry carries the SHA-256 of the previous entry, so any edit or deletion
in the middle breaks ``verify()``. This is the record regulators, users and
the robot's own metacognition read to answer "why did you do that?".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    ts: float
    actor: str
    action: str
    detail: dict[str, Any]
    prev_hash: str
    hash: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _digest(seq: int, ts: float, actor: str, action: str, detail: dict, prev: str) -> str:
    body = json.dumps([seq, ts, actor, action, detail, prev], sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class AuditLog:
    entries: list[AuditEntry] = field(default_factory=list)
    GENESIS = "0" * 64

    def record(self, ts: float, actor: str, action: str, **detail: Any) -> AuditEntry:
        prev = self.entries[-1].hash if self.entries else self.GENESIS
        seq = len(self.entries)
        h = _digest(seq, ts, actor, action, detail, prev)
        e = AuditEntry(seq, ts, actor, action, detail, prev, h)
        self.entries.append(e)
        return e

    def verify(self) -> tuple[bool, int]:
        """Returns (ok, first_bad_index). Index is -1 when the chain is intact."""
        prev = self.GENESIS
        for i, e in enumerate(self.entries):
            if e.prev_hash != prev or _digest(e.seq, e.ts, e.actor, e.action, e.detail, e.prev_hash) != e.hash:
                return False, i
            prev = e.hash
        return True, -1

    def tail(self, n: int = 20) -> list[dict]:
        return [e.to_dict() for e in self.entries[-n:]]

    def __len__(self) -> int:
        return len(self.entries)
