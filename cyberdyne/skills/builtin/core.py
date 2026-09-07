"""Built-in skills. Each one is deliberately tiny: the point is the contract."""
from __future__ import annotations

from typing import Any

from ...kernel.context import Context
from ..base import Skill, SkillManifest, SkillResult


class TimeSkill(Skill):
    manifest = SkillManifest("time", description="Report kernel time", tags=("info",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        return SkillResult(True, {"kernel_seconds": round(ctx.now, 3),
                                  "state": ctx.state.state.value})


class EchoSkill(Skill):
    manifest = SkillManifest("echo", description="Echo the arguments back",
                             args={"text": "text to echo"}, tags=("test",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        return SkillResult(True, args.get("text", ""))


class StatusSkill(Skill):
    manifest = SkillManifest("status", description="Summarise robot status", tags=("info",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        bus = ctx.bus
        return SkillResult(True, {
            "name": ctx.config.name,
            "state": ctx.state.state.value,
            "battery": bus.latest_payload("sensor/battery"),
            "pose": bus.latest_payload("sensor/odometry"),
            "brain": bus.latest_payload("brain/state"),
            "estop": ctx.safety.estop.engaged,
        })


class GotoSkill(Skill):
    manifest = SkillManifest("goto", description="Navigate to a point",
                             args={"x": "metres", "y": "metres", "name": "optional label"},
                             tags=("motion",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        try:
            x, y = float(args["x"]), float(args["y"])
        except (KeyError, TypeError, ValueError):
            return SkillResult(False, error="goto needs numeric x and y")
        w = ctx.config.world
        if not (0 <= x <= w.width and 0 <= y <= w.height):
            return SkillResult(False, error=f"({x}, {y}) is outside the {w.width}x{w.height} world")
        goal = {"x": x, "y": y, "name": args.get("name", "user")}
        # The brain owns navigation goals: it plans, tracks arrival and can pre-empt for safety.
        await ctx.bus.publish("brain/goal", {"goal": f"goto {x} {y} {goal['name']}"}, source="skill.goto")
        return SkillResult(True, goal)


class EStopSkill(Skill):
    manifest = SkillManifest("estop", description="Engage the emergency stop",
                             args={"reason": "why"}, tags=("safety",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        await ctx.bus.publish("safety/estop", {"engage": True,
                                              "reason": args.get("reason", "skill")}, source="skill.estop")
        return SkillResult(True, "e-stop engaged")


class EStopResetSkill(Skill):
    manifest = SkillManifest("estop_reset", description="Reset the emergency stop (needs confirmation)",
                             tags=("safety",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        await ctx.bus.publish("safety/estop", {"engage": False, "confirmed": True}, source="skill.estop_reset")
        return SkillResult(True, "e-stop reset")


class RememberSkill(Skill):
    manifest = SkillManifest("remember", description="Store a fact in working memory",
                             args={"key": "name", "value": "anything", "ttl": "seconds (optional)"},
                             tags=("memory",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        mem = ctx.extras.get("memory")
        if mem is None:
            return SkillResult(False, error="memory module not loaded")
        mem.working.set(args["key"], args.get("value"), args.get("ttl"))
        subject = args.get("subject", "self")
        mem.semantic.add(subject, args["key"], str(args.get("value")), "user", ctx.now)
        return SkillResult(True, {"stored": args["key"], "fact": f"{subject} {args['key']} {args.get('value')}"})


class RecallSkill(Skill):
    manifest = SkillManifest("recall", description="Recall recent important episodes",
                             args={"kind": "optional topic filter", "limit": "max results"},
                             tags=("memory",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        mem = ctx.extras.get("memory")
        if mem is None:
            return SkillResult(False, error="memory module not loaded")
        limit = int(args.get("limit", 5))
        if args.get("query"):
            eps = mem.episodic.search(str(args["query"]), limit)
        else:
            eps = mem.episodic.query(ctx.now, args.get("kind"), limit)
        facts = mem.semantic.neighbours(str(args["query"])) if args.get("query") else []
        return SkillResult(True, {"episodes": [{"ts": e.ts, "summary": e.summary} for e in eps],
                                  "facts": [f.to_dict() for f in facts]})


class PlanSkill(Skill):
    manifest = SkillManifest("plan", description="Hand a free-form goal to the brain's council",
                             args={"goal": "what to achieve, in words"}, tags=("cognition",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        goal = str(args.get("goal", "")).strip()
        if not goal:
            return SkillResult(False, error="plan needs a goal")
        await ctx.bus.publish("brain/goal", {"goal": goal, "confirmed": bool(args.get("confirmed"))},
                              source="skill.plan")
        return SkillResult(True, {"goal": goal})


class AnswerSkill(Skill):
    manifest = SkillManifest("answer", description="Answer the robot's open question",
                             args={"answer": "proceed | cancel | a new goal"}, tags=("cognition",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        await ctx.bus.publish("human/answer", {"answer": str(args.get("answer", "")),
                                               "id": args.get("id")}, source="skill.answer")
        return SkillResult(True, {"answer": args.get("answer")})


class ExplainSkill(Skill):
    manifest = SkillManifest("explain", description="Explain the brain's latest decision", tags=("cognition",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        brain = ctx.extras.get("brain")
        if brain is None or brain.council is None or not brain.council.history:
            return SkillResult(True, {"explanation": "no deliberation has happened yet",
                                      "mode": brain.mode if brain else None})
        d = brain.council.history[-1]
        return SkillResult(True, {
            "goal": d.goal,
            "decision": "approved" if d.approved else ("blocked" if d.hard_blocked else "held for human"),
            "confidence": round(d.confidence, 2),
            "rationale": d.plan.rationale,
            "steps": [s.__dict__ for s in d.plan.steps],
            "violations": [v.to_dict() for v in d.violations],
            "critique": d.critique,
            "prediction": d.rollout.to_dict() if d.rollout else None,
            "mode_now": brain.mode,
        })


class ForgetSkill(Skill):
    manifest = SkillManifest("forget", description="Erase everything remembered about a subject",
                             args={"about": "name or keyword"}, tags=("memory", "privacy"))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        mem = ctx.extras.get("memory")
        about = str(args.get("about", "")).strip()
        if mem is None or not about:
            return SkillResult(False, error="forget needs a subject and the memory module")
        result = mem.forget(about)
        ctx.safety.audit.record(ctx.now, "memory", "forget", about=about, **result)
        return SkillResult(True, {"forgot": about, **result})


class FindSkill(Skill):
    manifest = SkillManifest("find", description="Where is a person, object or room?",
                             args={"name": "what to look for"}, tags=("world",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        wm = ctx.extras.get("world_model")
        name = str(args.get("name", "")).strip()
        if wm is None or not name:
            return SkillResult(False, error="find needs a name and the world model")
        e = wm.find(name)
        if e is None:
            return SkillResult(True, {"found": False, "speech": f"I have not seen {name}."})
        age = ctx.now - e["last_seen"]
        where = f"in the {e['attrs'].get('room')}" if e["attrs"].get("room") else f"at ({e['x']:.1f}, {e['y']:.1f})"
        when = "now" if age < 5 else f"{age:.0f} seconds ago"
        return SkillResult(True, {"found": True, "entity": e,
                                  "speech": f"I last saw {name} {where}, {when}."})


class ArmSkill(Skill):
    manifest = SkillManifest("arm", description="Arm or disarm security mode",
                             args={"armed": "true|false"}, tags=("security",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        armed = str(args.get("armed", "true")).lower() in ("1", "true", "yes", "on")
        await ctx.bus.publish("security/arm", {"armed": armed}, source="skill.arm")
        ctx.safety.audit.record(ctx.now, "skills", "security.arm", armed=armed)
        return SkillResult(True, {"armed": armed, "speech": "Security armed." if armed else "Security disarmed."})


class DescribeSkill(Skill):
    manifest = SkillManifest("describe", description="Describe the scene (what and who is where)", tags=("world",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        wm = ctx.extras.get("world_model")
        scene = wm.scene if wm else {"summary": "no world model"}
        anomalies = [m.payload for m in ctx.bus.history("world/anomaly", 5)]
        return SkillResult(True, {"scene": scene, "anomalies": anomalies, "speech": scene.get("summary", "")})
