"""Self-authored skills, safety-bounded.

The robot (or its LLM, or a user) may compose NEW skills only as **macros**:
declarative lists of existing steps with argument templates. No code is ever
generated or executed, so a macro can be reviewed by the same constitution
as any plan. Creating a macro is the ``self.modify`` action, FORBIDDEN by
default and at most CONFIRM when an owner enables it in config.
"""
from __future__ import annotations

import re
from typing import Any

from ..cognition.constitution import Constitution
from ..cognition.planner import Plan, PlanStep
from ..kernel.context import Context
from ..tasks.model import TaskStep
from .base import Skill, SkillManifest, SkillResult

_TEMPLATE = re.compile(r"\{(\w+)\}")


def render(args: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            m = _TEMPLATE.fullmatch(v)
            out[k] = params.get(m[1], v) if m else _TEMPLATE.sub(lambda mm: str(params.get(mm[1], mm[0])), v)
        else:
            out[k] = v
    return out


class MacroSkill(Skill):
    def __init__(self, name: str, description: str, steps: list[dict], params: dict[str, str]) -> None:
        self.steps = steps
        self.manifest = SkillManifest(name, "0.1.0", description, args=params, tags=("macro", "user"))

    def to_dict(self) -> dict:
        return {"name": self.manifest.name, "description": self.manifest.description,
                "steps": self.steps, "params": dict(self.manifest.args)}

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        steps = [TaskStep.from_dict({**s, "args": render(s.get("args", {}), args)}) for s in self.steps]
        await ctx.bus.publish("task/start", {"name": self.manifest.name, "origin": f"macro:{self.manifest.name}",
                                             "steps": [s.to_dict() for s in steps]}, source="skill.macro")
        return SkillResult(True, {"task": self.manifest.name, "steps": len(steps)})


def validate_macro(spec: dict, constitution: Constitution, known: set[str]) -> list[str]:
    problems = []
    name = str(spec.get("name", ""))
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", name):
        problems.append("name must be lowercase identifier")
    if name in known:
        problems.append(f"{name} already exists")
    steps = spec.get("steps") or []
    if not steps:
        problems.append("no steps")
    plan_steps = []
    for s in steps:
        kind = s.get("kind") or ("goto" if s.get("skill") == "goto" else "skill")
        if kind == "goto":
            a = s.get("args", {})
            if any(isinstance(a.get(k), str) for k in ("x", "y")):
                continue                              # templated coordinates are checked at run time
            plan_steps.append(PlanStep("goto", a))
        elif kind == "skill":
            skill = s.get("args", {}).get("skill") or s.get("skill")
            if skill in ("self_modify", "define_skill"):
                problems.append("a macro may not define skills")
            plan_steps.append(PlanStep(str(skill), s.get("args", {}).get("args", {})))
        elif kind not in ("wait", "task"):
            problems.append(f"unknown step kind {kind}")
    for v in constitution.review(Plan(name, plan_steps)):
        if v.rule != "needs_confirmation":
            problems.append(f"{v.rule}: {v.detail}")
    return problems


class DefineSkill(Skill):
    manifest = SkillManifest("define_skill", description="Create a macro skill from existing steps",
                             action="self.modify",
                             args={"name": "identifier", "description": "text", "steps": "[{kind,args}]",
                                   "params": "{param: description}"}, tags=("meta",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        registry = ctx.extras["skills"]
        brain = ctx.extras.get("brain")
        constitution = brain.council.safety.constitution if brain and brain.council else Constitution(
            ctx.config, ctx.safety.permissions, set(registry.names()))
        problems = validate_macro(args, constitution, set(registry.names()))
        if problems:
            ctx.safety.audit.record(ctx.now, "skills", "define.rejected", name=args.get("name"), problems=problems)
            return SkillResult(False, error="; ".join(problems))
        macro = MacroSkill(str(args["name"]), str(args.get("description", "")), list(args["steps"]),
                           dict(args.get("params") or {}))
        registry.register(macro)
        if brain and brain.council:
            brain.council.safety.constitution.known_skills.add(macro.manifest.name)
        store = ctx.extras.get("store")
        if store:
            store.save_macro(macro.to_dict())
        ctx.safety.audit.record(ctx.now, "skills", "define", name=macro.manifest.name, steps=len(macro.steps))
        await ctx.bus.publish("skill/defined", macro.to_dict(), source="skill.define_skill")
        return SkillResult(True, {"defined": macro.manifest.name, "speech": f"New skill {macro.manifest.name} ready."})


DRAFT_SYSTEM = """You design a MACRO skill for a home robot: a short list of steps using ONLY existing skills.
Reply with ONE JSON object: {"name": "<lowercase_identifier>", "description": "<text>",
 "params": {"<param>": "<description>"}, "steps": [{"kind": "goto", "args": {"x": 1.0, "y": 2.0}} |
 {"kind": "skill", "args": {"skill": "<name>", "args": {...}}} | {"kind": "wait", "args": {"seconds": 2}}]}
Argument values may be templates like "{param}". Never target keep-out zones. Never use estop_reset or define_skill.
Available skills and places are in the request."""


class DraftSkill(Skill):
    """Ask the model to draft a macro, then put it through exactly the same validation as a human-authored one."""
    manifest = SkillManifest("draft_skill", description="Let the model draft a macro skill from a description",
                             action="self.modify", args={"description": "what the new skill should do",
                                                         "name": "optional identifier"}, tags=("meta", "llm"))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        import json

        from ..cognition.llm import LLMError
        backend = ctx.extras.get("llm")
        if backend is None:
            return SkillResult(False, error="no LLM backend configured")
        registry = ctx.extras["skills"]
        wm = ctx.extras.get("world_model")
        req = {"request": str(args.get("description", "")), "suggested_name": args.get("name"),
               "skills": [{"name": s["name"], "args": s["args"]} for s in registry.describe()
                          if s["name"] not in ("define_skill", "draft_skill", "estop_reset")],
               "places": wm.places() if wm else {}, "keep_out": ctx.config.safety.keep_out}
        try:
            spec = (await backend.complete(DRAFT_SYSTEM, json.dumps(req, default=str), effort="high")).json()
        except (LLMError, ValueError) as exc:
            return SkillResult(False, error=f"draft failed: {exc}")
        spec.setdefault("name", args.get("name") or "drafted")
        ctx.safety.audit.record(ctx.now, "skills", "draft", name=spec.get("name"), steps=len(spec.get("steps") or []))
        define = registry.get("define_skill")
        result = await define.run(ctx, {**spec, "confirmed": True})
        if result.ok:
            result.output["drafted"] = spec
        return result
