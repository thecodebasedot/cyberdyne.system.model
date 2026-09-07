"""Semantic memory: a tiny knowledge graph of (subject, predicate, object) facts."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    subject: str
    predicate: str
    object: str
    source: str = ""
    ts: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class KnowledgeGraph:
    def __init__(self) -> None:
        self._facts: list[Fact] = []

    def add(self, subject: str, predicate: str, obj: str, source: str = "", ts: float = 0.0) -> Fact:
        s, p, o = subject.strip().lower(), predicate.strip().lower(), obj.strip()
        self._facts = [f for f in self._facts if not (f.subject == s and f.predicate == p)]  # newest wins
        f = Fact(s, p, o, source, ts)
        self._facts.append(f)
        return f

    def query(self, subject: str | None = None, predicate: str | None = None,
              obj: str | None = None) -> list[Fact]:
        return [f for f in self._facts
                if (subject is None or f.subject == subject.lower())
                and (predicate is None or f.predicate == predicate.lower())
                and (obj is None or f.object.lower() == obj.lower())]

    def get(self, subject: str, predicate: str) -> str | None:
        r = self.query(subject, predicate)
        return r[0].object if r else None

    def neighbours(self, node: str) -> list[Fact]:
        n = node.lower()
        return [f for f in self._facts if f.subject == n or f.object.lower() == n]

    def forget(self, about: str) -> int:
        a = about.lower()
        before = len(self._facts)
        self._facts = [f for f in self._facts
                       if a not in (f.subject, f.object.lower()) and a not in f.object.lower()]
        return before - len(self._facts)

    def all(self) -> list[dict]:
        return [f.to_dict() for f in self._facts]

    def __len__(self) -> int:
        return len(self._facts)
