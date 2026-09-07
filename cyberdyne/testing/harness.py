from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from ..kernel.config import RobotConfig
from ..runtime import Runtime
from ..skills.base import SkillResult


class TestRobot:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.bus = rt.bus
        self.received: list = []
        rt.bus.subscribe("*", lambda m: self.received.append(m), name="harness.tap")

    async def run(self, seconds: float) -> None:
        await self.rt.run(seconds)

    async def invoke(self, skill: str, args: dict | None = None, confirmed: bool = False,
                     trust: str = "owner") -> SkillResult:
        runner = self.rt.scheduler.get("skills")
        return await runner.invoke(skill, args or {}, confirmed, trust)

    async def say(self, text: str, run: float = 1.0) -> list[dict]:
        await self.rt.say(text)
        before = len(self.received)
        await self.rt.run(run)
        return [m.payload for m in self.received[before:] if m.topic == "skill/result"]

    async def wait_for(self, topic: str, timeout: float = 30.0, step: float = 0.5):
        """Advance simulation until ``topic`` is published; returns the payload or raises TimeoutError."""
        start = len(self.received)
        t = 0.0
        while t < timeout:
            await self.rt.run(step)
            t += step
            for m in self.received[start:]:
                if m.topic == topic:
                    return m.payload
        raise TimeoutError(f"{topic} not seen within {timeout} sim seconds")

    def messages(self, topic: str) -> list:
        return [m.payload for m in self.received if m.topic == topic]

    @property
    def report(self) -> dict:
        return self.rt.report()


@asynccontextmanager
async def robot(config: RobotConfig | None = None, *, patrol: bool = False, strict: bool = True,
                skills: list[str] | None = None):
    cfg = config or RobotConfig()
    if not patrol:
        cfg.brain.patrol = []
    cfg.kernel.strict_bus = strict
    if skills:
        cfg.skills = list(cfg.skills) + list(skills)
    rt = Runtime(cfg)
    await rt.boot()
    tr = TestRobot(rt)
    try:
        yield tr
    finally:
        await rt.shutdown()


def run(coro):
    return asyncio.run(coro)
