"""Planner interface.

``RulePlanner`` turns a goal string into a list of skill invocations using
hand-written rules. An LLM-backed planner implements the same ``plan``
signature and can be dropped in via ``Brain.planner``.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PlanStep:
    skill: str
    args: dict = field(default_factory=dict)
    note: str = ""


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep]
    rationale: str = ""

    def to_dict(self) -> dict:
        return {"goal": self.goal, "rationale": self.rationale,
                "steps": [s.__dict__ for s in self.steps]}


class Planner(ABC):
    @abstractmethod
    async def plan(self, goal: str, context: dict) -> Plan: ...


_SEQ = re.compile(r"\s*(?:,?\s+then\s+|,?\s+tarpor\s+|,?\s+ar\s+|,?\s+and then\s+|\s*;\s*)\s*")


class RulePlanner(Planner):
    async def plan(self, goal: str, context: dict) -> Plan:
        g = goal.lower().strip()
        parts = [x for x in _SEQ.split(g) if x]
        if len(parts) > 1:                                      # hierarchical: decompose, plan each, concatenate
            subplans = [await self.plan(part, context) for part in parts]
            failed = [sp for sp in subplans if not sp.steps]
            if failed:
                return Plan(goal, [], "could not plan: " + "; ".join(f"'{sp.goal}' ({sp.rationale})" for sp in failed))
            return Plan(goal, [st for sp in subplans for st in sp.steps],
                        " then ".join(f"[{sp.goal}: {sp.rationale}]" for sp in subplans))
        tasks = context.get("tasks") or []
        if g in tasks or g.removeprefix("run ").removeprefix("do ") in tasks:
            name = g.removeprefix("run ").removeprefix("do ")
            return Plan(goal, [PlanStep("task", {"name": name})], f"'{name}' is a library task")
        if g in ("charge", "go charge", "recharge"):
            c = context["charger"]
            return Plan(goal, [PlanStep("goto", {"x": c["x"], "y": c["y"], "name": "charger"})],
                        "battery policy")
        if g.startswith("patrol"):
            steps = [PlanStep("goto", {**p, "name": f"waypoint_{i}"})
                     for i, p in enumerate(context.get("patrol", []))]
            return Plan(goal, steps, "visit every patrol waypoint in order")
        m = re.match(r"^goto\s+(-?[\d.]+)[ ,]+(-?[\d.]+)(?:\s+(\w+))?$", g)
        if m:
            return Plan(goal, [PlanStep("goto", {"x": float(m[1]), "y": float(m[2]),
                                                 "name": m[3] or "user"})], "direct navigation request")
        m = re.match(r"^(?:goto|go to|find|go near)\s+([\w ]+?)$", g)
        if m:
            name = m[1].strip()
            places = {k.lower(): v for k, v in (context.get("places") or {}).items()}
            if name in places:
                x, y = places[name]
                return Plan(goal, [PlanStep("goto", {"x": x, "y": y, "name": name})], f"'{name}' is a known place")
            return Plan(goal, [], f"I don't know where '{name}' is")
        if g in ("stop", "halt"):
            return Plan(goal, [PlanStep("estop", {"reason": "planner"})], "user asked to stop")
        return Plan(goal, [], "no rule matched")
