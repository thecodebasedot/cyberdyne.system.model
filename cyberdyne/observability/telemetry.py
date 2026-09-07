"""Telemetry: one place that can describe the whole robot as JSON."""
from __future__ import annotations

from ..kernel.context import Context
from ..kernel.module import Module


class Telemetry(Module):
    name = "telemetry"
    rate_hz = 2.0
    priority = 90

    def __init__(self, scheduler=None) -> None:
        super().__init__()
        self._scheduler = scheduler
        self.snapshot: dict = {}

    def attach(self, scheduler) -> None:
        self._scheduler = scheduler

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        ctx.extras["telemetry"] = self

    def build(self) -> dict:
        ctx = self.ctx
        bus = ctx.bus
        wm = ctx.extras.get("world_model")
        mem = ctx.extras.get("memory")
        snap = {
            "name": ctx.config.name,
            "time": ctx.now,
            "state": ctx.state.state.value,
            "safety": ctx.safety.describe(),
            "audit_tail": ctx.safety.audit.tail(8),
            "battery": bus.latest_payload("sensor/battery"),
            "pose": bus.latest_payload("sensor/odometry"),
            "brain": bus.latest_payload("brain/state"),
            "nav": bus.latest_payload("nav/status"),
            "path": bus.latest_payload("nav/path"),
            "decision": bus.latest_payload("brain/decision"),
            "question": (bus.latest_payload("brain/question")
                         if (bus.latest_payload("brain/state") or {}).get("question") else None),
            "llm": ctx.extras["llm"].describe() if ctx.extras.get("llm") else None,
            "facts": mem.semantic.all()[-10:] if mem else [],
            "scan": bus.latest_payload("perception/scan"),
            "front_clearance": bus.latest_payload("perception/front_clearance"),
            "cmd": bus.latest_payload("motion/cmd_applied"),
            "world": ctx.world.to_dict() if ctx.world else None,
            "world_model": wm.snapshot() if wm else None,
            "memory": bus.latest_payload("memory/summary"),
            "episodes": [e.to_dict() for e in mem.episodic.recent(8)] if mem else [],
            "bus": {"published": bus.stats.published, "delivered": bus.stats.delivered,
                    "errors": bus.stats.handler_errors, "topics": bus.topics()},
            "scheduler": self._scheduler.describe() if self._scheduler else None,
            "skills": ctx.extras["skills"].describe() if "skills" in ctx.extras else [],
            "recent": [m.to_dict() for m in bus.history("*", 25)
                       if not m.topic.startswith(("sensor/", "perception/", "motion/", "nav/status", "nav/path",
                                                  "brain/state", "world/", "memory/"))],
        }
        return snap

    async def tick(self, dt: float) -> None:
        self.snapshot = self.build()
        await self.ctx.bus.publish("telemetry/heartbeat",
                                   {"time": self.snapshot["time"], "state": self.snapshot["state"]},
                                   source=self.name)
