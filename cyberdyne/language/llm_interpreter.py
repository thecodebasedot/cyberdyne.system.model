"""LLMInterpreter: rules first (fast, free, deterministic), the model for the rest."""
from __future__ import annotations

import json
import logging

from ..cognition.llm import LLMBackend, LLMError
from .interpreter import Intent, Interpreter, RuleInterpreter

log = logging.getLogger("cyberdyne.llm_interpreter")

SYSTEM = """You map a user's utterance to ONE robot skill. Users write English, Bangla, or romanised Bangla.
Reply with ONE JSON object only: {"skill": "<name or null>", "args": {...}, "confidence": 0..1}
Use only these skills and argument names:
{skills}
If the utterance is a free-form task that no single skill covers, use skill "plan"
with args {"goal": "<restated goal in English>"}.
If it is not a command at all, return {"skill": null, "args": {}, "confidence": 0}.
"""


class LLMInterpreter(Interpreter):
    def __init__(self, backend: LLMBackend, skills: list[dict] | None = None,
                 rules: Interpreter | None = None, min_confidence: float = 0.5) -> None:
        self.backend = backend
        self.rules = rules or RuleInterpreter()
        self.min_confidence = min_confidence
        self.skills = skills or []
        self.llm_calls = 0

    def parse(self, text: str) -> Intent | None:
        return self.rules.parse(text)

    async def parse_async(self, text: str) -> Intent | None:
        intent = self.rules.parse(text)
        if intent is not None:
            return intent
        self.llm_calls += 1
        skills = "\n".join(f"- {s['name']}: {s['description']} args={json.dumps(s['args'])}" for s in self.skills)
        try:
            resp = await self.backend.complete(SYSTEM.replace("{skills}", skills), text, effort="low",
                                               max_tokens=400)
            data = resp.json()
        except (LLMError, ValueError) as exc:
            log.warning("llm interpreter failed: %s", exc)
            return None
        skill = data.get("skill")
        conf = float(data.get("confidence", 0) or 0)
        if not skill or conf < self.min_confidence:
            return None
        return Intent(str(skill), dict(data.get("args") or {}), conf, text)
