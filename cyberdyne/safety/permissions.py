"""Action permission tiers.

    FREE      -> just do it
    LOG       -> do it, write an audit entry
    CONFIRM   -> needs explicit human confirmation token
    FORBIDDEN -> never

Rules are ``glob -> tier``; the most specific (longest) matching glob wins.
"""
from __future__ import annotations

import fnmatch
from enum import StrEnum


class Tier(StrEnum):
    FREE = "free"
    LOG = "log"
    CONFIRM = "confirm"
    FORBIDDEN = "forbidden"


class PermissionDenied(PermissionError):
    def __init__(self, action: str, tier: Tier) -> None:
        super().__init__(f"{action} is {tier.value}")
        self.action = action
        self.tier = tier


DEFAULT_RULES: dict[str, str] = {
    "*": "log",
    "skill.time": "free",
    "skill.echo": "free",
    "skill.status": "free",
    "skill.goto": "log",
    "skill.estop": "free",
    "skill.estop_reset": "confirm",
    "skill.plan": "log",
    "skill.answer": "log",
    "skill.explain": "free",
    "skill.forget": "log",
    "skill.remember": "free",
    "skill.recall": "free",
    "safety.*": "forbidden",       # nothing may reconfigure the safety core at runtime
    "self.modify": "forbidden",
}


class PermissionPolicy:
    def __init__(self, rules: dict[str, str] | None = None) -> None:
        merged = dict(DEFAULT_RULES)
        merged.update(rules or {})
        self.rules: dict[str, Tier] = {k: Tier(v) for k, v in merged.items()}

    def tier(self, action: str) -> Tier:
        best, best_len = Tier.LOG, -1
        for pattern, tier in self.rules.items():
            if fnmatch.fnmatchcase(action, pattern) and len(pattern) > best_len:
                best, best_len = tier, len(pattern)
        return best

    def check(self, action: str, confirmed: bool = False) -> Tier:
        t = self.tier(action)
        if t == Tier.FORBIDDEN or (t == Tier.CONFIRM and not confirmed):
            raise PermissionDenied(action, t)
        return t

    def describe(self) -> dict[str, str]:
        return {k: v.value for k, v in self.rules.items()}
