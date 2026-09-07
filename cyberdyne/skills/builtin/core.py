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
        return SkillResult(True, {"stored": args["key"]})


class RecallSkill(Skill):
    manifest = SkillManifest("recall", description="Recall recent important episodes",
                             args={"kind": "optional topic filter", "limit": "max results"},
                             tags=("memory",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        mem = ctx.extras.get("memory")
        if mem is None:
            return SkillResult(False, error="memory module not loaded")
        eps = mem.episodic.query(ctx.now, args.get("kind"), int(args.get("limit", 5)))
        return SkillResult(True, [{"ts": e.ts, "summary": e.summary} for e in eps])
