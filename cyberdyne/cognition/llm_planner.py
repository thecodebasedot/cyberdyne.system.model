"""LLMPlanner: goal + situation -> Plan via a language model, with the rule
planner as the fallback and the constitution as the judge (in the council).
"""
from __future__ import annotations

import json
import logging

from .llm import LLMBackend, LLMError
from .planner import Plan, Planner, PlanStep, RulePlanner

log = logging.getLogger("cyberdyne.llm_planner")

SYSTEM = """You are the planning module of a mobile robot called Cyberdyne.
You receive a goal and a JSON situation report. Reply with ONE JSON object and nothing else:
{"rationale": "<one sentence>", "steps": [{"skill": "<name>", "args": {...}}, ...]}

Rules:
- Use only the skills listed in the situation report, with the argument names given there.
- Coordinates are metres inside the world bounds. Never target keep-out zones or obstacles.
- Prefer few steps. If the goal is impossible or unsafe, return an empty steps list and say why.
- Do not invent skills, do not reset the emergency stop, do not modify the robot itself.
"""


class LLMPlanner(Planner):
    def __init__(self, backend: LLMBackend, fallback: Planner | None = None, effort: str = "high") -> None:
        self.backend = backend
        self.fallback = fallback or RulePlanner()
        self.effort = effort
        self.calls = 0
        self.fallbacks = 0

    async def plan(self, goal: str, context: dict) -> Plan:
        self.calls += 1
        prompt = f"GOAL: {goal}\n\nSITUATION:\n{json.dumps(context, sort_keys=True, default=str)}"
        try:
            resp = await self.backend.complete(SYSTEM, prompt, effort=self.effort)
            data = resp.json()
            steps = [PlanStep(str(s["skill"]), dict(s.get("args") or {}), str(s.get("note", "")))
                     for s in data.get("steps", [])]
            return Plan(goal, steps, str(data.get("rationale", "")) or "llm")
        except (LLMError, ValueError, KeyError, TypeError) as exc:
            self.fallbacks += 1
            log.warning("llm planner fell back to rules: %s", exc)
            plan = await self.fallback.plan(goal, context)
            plan.rationale = f"[rule fallback: {exc}] {plan.rationale}"
            return plan
