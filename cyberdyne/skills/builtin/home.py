"""Home, task, routine and teaching skills."""
from __future__ import annotations

from typing import Any

from ...kernel.context import Context
from ...tasks.routines import Routine
from ..base import Skill, SkillManifest, SkillResult


class DeviceSkill(Skill):
    manifest = SkillManifest("device", description="Switch a smart-home device",
                             args={"kind": "light|fan|door|plug", "room": "room name (default: current)",
                                   "name": "device id/name", "on": "true|false"}, tags=("home",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        hub = ctx.extras.get("home")
        if hub is None:
            return SkillResult(False, error="no home hub configured")
        wm = ctx.extras.get("world_model")
        room = args.get("room") or (wm.current_room if wm else None)
        devs = hub.find(args.get("kind"), room if not args.get("name") else None, args.get("name"))
        if not devs and room and not args.get("name"):
            devs = hub.find(args.get("kind"), None, None)        # fall back to every room
        if not devs:
            return SkillResult(False, error=f"no {args.get('kind') or 'device'} in {room or 'any room'}")
        on = str(args.get("on", "true")).lower() in ("1", "true", "yes", "on")
        for d in devs:
            await hub.set_state(d, on=on)
            await ctx.bus.publish("home/device", d.to_dict(), source="skill.device")
        names = ", ".join(f"{d.room} {d.kind}".strip() for d in devs)
        return SkillResult(True, {"devices": [d.to_dict() for d in devs],
                                  "speech": f"{names} {'on' if on else 'off'}."})


class RemindSkill(Skill):
    manifest = SkillManifest("remind", description="Say something after a delay",
                             args={"seconds": "delay", "text": "what to say"}, tags=("routine",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        rm = ctx.extras.get("routines")
        if rm is None:
            return SkillResult(False, error="routines module not loaded")
        secs = float(args.get("seconds", 60))
        text = str(args.get("text", "reminder"))
        r = rm.add(Routine(f"reminder:{text[:20]}", once_at=ctx.now + secs, speak=f"Reminder: {text}", origin="user"))
        return SkillResult(True, {"at": r.next_due, "speech": f"I will remind you in {secs:.0f} seconds."})


class TaskSkill(Skill):
    manifest = SkillManifest("task", description="Start, pause, resume or cancel a task",
                             args={"name": "library task name", "action": "start|pause|resume|cancel"},
                             tags=("task",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        action = str(args.get("action", "start"))
        if action == "start":
            runner = ctx.extras.get("tasks")
            name = str(args.get("name", ""))
            if runner is None or name not in runner.library:
                return SkillResult(False, error=f"unknown task {name!r}")
            await ctx.bus.publish("task/start", {"name": name, "origin": "skill.task"}, source="skill.task")
            return SkillResult(True, {"started": name, "speech": f"Starting {name}."})
        if action in ("pause", "resume", "cancel"):
            await ctx.bus.publish(f"task/{action}", {"reason": "user"}, source="skill.task")
            return SkillResult(True, {"action": action})
        return SkillResult(False, error=f"unknown action {action}")


class TeachSkill(Skill):
    """Demonstration learning: record where the robot is sent, replay as a task."""
    manifest = SkillManifest("teach", description="Record a route by demonstration",
                             args={"action": "start|stop", "name": "route name"}, tags=("learning",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        rec = ctx.extras.get("recorder")
        if rec is None:
            return SkillResult(False, error="recorder not loaded")
        action = str(args.get("action", "start"))
        name = str(args.get("name", "demo"))
        if action == "start":
            rec.start(name)
            return SkillResult(True, {"recording": name, "speech": f"Recording {name}. Show me where to go."})
        name = rec.recording or name
        steps = rec.stop()
        if not steps:
            return SkillResult(False, error="nothing was demonstrated")
        ctx.extras["tasks"].define(name, steps)
        store = ctx.extras.get("store")
        if store:
            store.save_task(name, [s.to_dict() for s in steps])
        return SkillResult(True, {"task": name, "steps": len(steps),
                                  "speech": f"Learned {name} with {len(steps)} steps."})
