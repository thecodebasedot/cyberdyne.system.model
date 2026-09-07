"""Who (or what) is this? Signatures (face / voice / object embeddings in a
real robot) map to named people and objects. People carry a trust level,
which gates what a speaker is allowed to ask for.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Trust(StrEnum):
    OWNER = "owner"
    GUEST = "guest"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {"owner": 2, "guest": 1, "unknown": 0}[self.value]


@dataclass
class Person:
    name: str
    signature: str
    trust: Trust = Trust.GUEST

    def to_dict(self) -> dict:
        return {"name": self.name, "signature": self.signature, "trust": self.trust.value}


class IdentityRegistry:
    def __init__(self, known: list[dict] | None = None) -> None:
        self._by_sig: dict[str, Person] = {}
        for k in known or []:
            self.enroll(k["name"], k["signature"], Trust(k.get("trust", "guest")))

    def enroll(self, name: str, signature: str, trust: Trust = Trust.GUEST) -> Person:
        p = Person(name, signature, trust)
        self._by_sig[signature] = p
        return p

    def identify(self, signature: str) -> Person | None:
        return self._by_sig.get(signature) if signature else None

    def trust_of(self, signature: str) -> Trust:
        p = self.identify(signature)
        return p.trust if p else Trust.UNKNOWN

    def forget(self, name: str) -> int:
        gone = [s for s, p in self._by_sig.items() if p.name.lower() == name.lower()]
        for s in gone:
            del self._by_sig[s]
        return len(gone)

    def all(self) -> list[dict]:
        return [p.to_dict() for p in self._by_sig.values()]
