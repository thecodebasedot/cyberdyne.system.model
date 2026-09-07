"""DemoRecorder: learning from demonstration.

While recording, every navigation goal the human sends (dashboard click,
``goto`` skill) and every device command becomes a step. ``stop()`` returns
the steps as a task the runner can replay.
"""
from __future__ import annotations

from ..kernel.context import Context
from ..kernel.module import Module
from ..tasks.model import TaskStep


class DemoRecorder(Module):
    name = "recorder"
    rate_hz = 1.0
    priority = 70

    def __init__(self) -> None:
        super().__init__()
        self.recording: str | None = None
        self.steps: list[TaskStep] = []

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._subs = [ctx.bus.subscribe("nav/goal", self._on_goal, name="recorder.goal"),
                      ctx.bus.subscribe("home/device", self._on_device, name="recorder.device")]
        ctx.extras["recorder"] = self

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    def start(self, name: str) -> None:
        self.recording, self.steps = name, []

    def stop(self) -> list[TaskStep]:
        steps, self.recording, self.steps = self.steps, None, []
        return steps

    def _on_goal(self, msg) -> None:
        if self.recording and msg.source not in ("tasks", "brain"):       # only human-sent goals
            p = msg.payload
            self.steps.append(TaskStep("goto", {"x": p["x"], "y": p["y"], "name": p.get("name", "demo")}))

    def _on_device(self, msg) -> None:
        if self.recording:
            d = msg.payload
            self.steps.append(TaskStep("skill", {"skill": "device",
                                                 "args": {"name": d["id"], "on": d["state"].get("on")}}))

    async def tick(self, dt: float) -> None:
        if self.recording:
            await self.ctx.bus.publish("recorder/status", {"recording": self.recording, "steps": len(self.steps)},
                                       source=self.name)
