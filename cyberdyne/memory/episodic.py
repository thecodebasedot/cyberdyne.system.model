"""Episodic memory: an append-only timeline of significant events.

Retrieval scores by recency and importance so "what happened recently that
mattered" is a single call. Vector search slots in later behind ``query``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .index import Embedder, TextIndex


@dataclass
class Episode:
    ts: float
    kind: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class EpisodicMemory:
    def __init__(self, capacity: int = 5000, embedder: Embedder | None = None) -> None:
        self.capacity = capacity
        self._episodes: list[Episode] = []
        self._index = TextIndex(embedder)
        self._seq = 0

    def remember(self, ts: float, kind: str, summary: str, importance: float = 0.5, **data) -> Episode:
        ep = Episode(ts, kind, summary, data, importance)
        ep.data.setdefault("_id", self._seq)
        self._index.add(self._seq, f"{kind} {summary}")
        self._seq += 1
        self._episodes.append(ep)
        if len(self._episodes) > self.capacity:
            self.consolidate()
        return ep

    def consolidate(self) -> int:
        """Forget the least important half of the oldest quarter."""
        n = len(self._episodes) // 4
        old, rest = self._episodes[:n], self._episodes[n:]
        old.sort(key=lambda e: e.importance)
        dropped = old[: len(old) // 2]
        self._index.remove({e.data["_id"] for e in dropped})
        self._episodes = sorted(old[len(dropped):], key=lambda e: e.ts) + rest
        return len(dropped)

    def search(self, text: str, limit: int = 5, min_score: float = 0.05) -> list[Episode]:
        """Similarity search over summaries (vector-backed; embedder is pluggable)."""
        by_id = {e.data["_id"]: e for e in self._episodes}
        return [by_id[k] for k, score in self._index.search(text, limit * 2)
                if k in by_id and score >= min_score][:limit]

    def forget(self, about: str) -> int:
        a = about.lower()
        gone = [e for e in self._episodes if a in e.summary.lower()]
        self._index.remove({e.data["_id"] for e in gone})
        self._episodes = [e for e in self._episodes if e not in gone]
        return len(gone)

    def query(self, now: float, kind: str | None = None, limit: int = 10,
              half_life: float = 60.0) -> list[Episode]:
        def score(e: Episode) -> float:
            age = max(0.0, now - e.ts)
            return e.importance * math.exp(-age * math.log(2) / half_life)
        cands = [e for e in self._episodes if kind is None or e.kind == kind]
        return sorted(cands, key=score, reverse=True)[:limit]

    def recent(self, limit: int = 10) -> list[Episode]:
        return self._episodes[-limit:]

    def __len__(self) -> int:
        return len(self._episodes)
