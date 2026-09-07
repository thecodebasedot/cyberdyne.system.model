"""Evaluate the LLM interpreter and planner against a fixed set of cases.

    cyberdyne eval-llm                 # needs ANTHROPIC_API_KEY (or an `ant auth login` profile)
    cyberdyne eval-llm --backend scripted   # dry run through the rule fallbacks

Each case is an utterance the rule interpreter does NOT understand, with the
skill (and key arguments) the model should map it to; planner cases are
goals with the expected first step. Reports accuracy and token usage.
"""
from __future__ import annotations

import asyncio
import json

from ..kernel.config import RobotConfig
from ..runtime import Runtime
from .llm import LLMBackend, ScriptedBackend, build_backend

INTERPRETER_CASES = [
    {"text": "please head over to the kitchen", "skill": "plan", "args": {"goal": "goto kitchen"}},
    {"text": "ektu living room e giye light ta jalao", "skill": "plan"},
    {"text": "everything off in the living room", "skill": "device", "args": {"on": False}},
    {"text": "what have you been up to", "skill": "recall"},
    {"text": "ami ke", "skill": "recall"},
    {"text": "freeze right now!", "skill": "estop"},
    {"text": "the weather is lovely", "skill": None},
    {"text": "set a reminder for two minutes from now to call amma", "skill": "remind", "args": {"seconds": 120}},
]
PLANNER_CASES = [
    {"goal": "check the kitchen then come back to the charger", "first_skill": "goto", "min_steps": 2},
    {"goal": "turn on the living room light", "first_skill": "device"},
    {"goal": "go to the stairs", "expect_empty_or_question": True},
]


async def run_eval(backend: LLMBackend, config: RobotConfig | None = None) -> dict:
    cfg = config or RobotConfig()
    cfg.world.rooms = [{"name": "kitchen", "x": 4, "y": 0, "w": 6, "h": 3},
                       {"name": "living", "x": 4, "y": 3, "w": 6, "h": 5}]
    cfg.home.devices = [{"id": "living_light", "kind": "light", "room": "living"}]
    cfg.safety.keep_out = [{"x": 0, "y": 8, "w": 1, "h": 2, "name": "stairs"}]
    cfg.brain.llm = "scripted"
    rt = Runtime(cfg, llm=backend)
    await rt.boot()
    await rt.run(1)
    interp = rt.scheduler.get("language").interpreter
    report = {"interpreter": [], "planner": []}
    for case in INTERPRETER_CASES:
        intent = await interp.parse_async(case["text"])
        got = intent.skill if intent else None
        ok = got == case["skill"]
        if ok and intent and case.get("args"):
            ok = all(str(intent.args.get(k)).lower().startswith(str(v).lower()[:6]) for k, v in case["args"].items())
        report["interpreter"].append({"text": case["text"], "expected": case["skill"], "got": got,
                                      "args": intent.args if intent else None, "ok": ok})
    for case in PLANNER_CASES:
        d = await rt.brain.council.deliberate(case["goal"])
        first = d.plan.steps[0].skill if d.plan.steps else None
        if case.get("expect_empty_or_question"):
            ok = not d.approved
        else:
            ok = first == case["first_skill"] and len(d.plan.steps) >= case.get("min_steps", 1)
        report["planner"].append({"goal": case["goal"], "steps": [s.__dict__ for s in d.plan.steps],
                                  "approved": d.approved, "confidence": round(d.confidence, 2),
                                  "rationale": d.plan.rationale, "ok": ok})
    await rt.shutdown()
    n_ok = sum(r["ok"] for r in report["interpreter"]) + sum(r["ok"] for r in report["planner"])
    n = len(report["interpreter"]) + len(report["planner"])
    report["summary"] = {"passed": n_ok, "total": n, "accuracy": round(n_ok / n, 3), "backend": backend.describe()}
    return report


def main(kind: str = "anthropic", model: str = "claude-opus-5", effort: str = "high") -> dict:
    backend = ScriptedBackend([]) if kind == "scripted" else build_backend(kind, model, effort)
    return asyncio.run(run_eval(backend))


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(main(), indent=2))
