"""Command interpreter.

``RuleInterpreter`` maps text to a skill invocation with regular expressions,
in English and romanised Bangla. It is the fallback and the test oracle for
the LLM interpreter that replaces it in a later phase (same ``parse`` API).
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Intent:
    skill: str
    args: dict = field(default_factory=dict)
    confidence: float = 1.0
    raw: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class Interpreter(ABC):
    @abstractmethod
    def parse(self, text: str) -> Intent | None: ...

    async def parse_async(self, text: str) -> Intent | None:
        return self.parse(text)


_NUM = r"(-?\d+(?:\.\d+)?)"
_RULES: list[tuple[re.Pattern, str, callable]] = [
    (re.compile(rf"^(?:go ?to|move to|jao|cholo|jaw)\s+{_NUM}[ ,]+{_NUM}$"), "goto",
     lambda m: {"x": float(m[1]), "y": float(m[2])}),
    (re.compile(r"^(?:go )?(?:charge|recharge|dock|charge koro|charging e jao)$"), "goto",
     lambda m: {"charger": True}),
    (re.compile(r"^(?:stop|halt|freeze|emergency|thamo|thamo!|bondho koro|estop)$"), "estop",
     lambda m: {"reason": "voice command"}),
    (re.compile(r"^(?:resume|reset estop|abar cholo|continue|chalu koro)$"), "estop_reset", lambda m: {}),
    (re.compile(r"^(?:status|report|kemon acho|ki obostha|obostha ki)$"), "status", lambda m: {}),
    (re.compile(r"^(?:time|what time is it|koyta baje|somoy koto)$"), "time", lambda m: {}),
    (re.compile(r"^(?:say|echo|bolo)\s+(.+)$"), "echo", lambda m: {"text": m[1]}),
    (re.compile(r"^(?:remember|mone rakho)\s+(\w+)\s*(?:=|is|holo)\s*(.+)$"), "remember",
     lambda m: {"key": m[1], "value": m[2]}),
    (re.compile(r"^(?:recall|what happened|ki hoyeche|mone koro)$"), "recall", lambda m: {}),
    (re.compile(r"^(?:recall|search memory|khojo)\s+(.+)$"), "recall", lambda m: {"query": m[1]}),
    (re.compile(r"^(?:explain|why|keno|why did you do that|keno korle)$"), "explain", lambda m: {}),
    (re.compile(r"^(?:forget|bhule jao)\s+(.+)$"), "forget", lambda m: {"about": m[1]}),
    (re.compile(r"^(?:answer|uttor|reply)\s+(.+)$"), "answer", lambda m: {"answer": m[1]}),
    (re.compile(r"^(?:yes|proceed|ha|hae|go ahead|thik ache)$"), "answer", lambda m: {"answer": "proceed"}),
    (re.compile(r"^(?:no|cancel|na|bad dao)$"), "answer", lambda m: {"answer": "cancel"}),
    (re.compile(r"^(?:plan|do|task|koro|kaj koro)\s+(.+)$"), "plan", lambda m: {"goal": m[1]}),
    (re.compile(r"^(?:patrol|patrol koro|ghuro|start patrol)$"), "plan", lambda m: {"goal": "patrol"}),
]


class RuleInterpreter(Interpreter):
    def parse(self, text: str) -> Intent | None:
        t = re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?")
        for pattern, skill, extract in _RULES:
            m = pattern.match(t)
            if m:
                return Intent(skill, extract(m), 1.0, text)
        return None
