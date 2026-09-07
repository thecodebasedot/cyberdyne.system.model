"""RoutineModule: time-based triggers.

    [[routines]] name = "night patrol", every = 600, task = "patrol"      # seconds
    [[routines]] name = "morning",      at = "07:30",  goal = "goto kitchen", speak = "Good morning"

``every`` fires on the kernel clock; ``at`` fires once per day on the wall
clock (HH:MM). One-shot reminders are added at runtime by the ``remind``
skill and removed once fired.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..kernel.context import Context
from ..kernel.module import Module


@dataclass
class Routine:
    name: str
    every: float | None = None
    at: str | None = None
    once_at: float | None = None          # kernel time, one-shot
    goal: str | None = None
    task: str | None = None
    speak: str | None = None
    next_due: float = 0.0
    fired: int = 0
    last_day: str = ""
    origin: str = "config"

    def to_dict(self) -> dict:
        return {"name": self.name, "every": self.every, "at": self.at, "once_at": self.once_at,
                "goal": self.goal, "task": self.task, "speak": self.speak, "fired": self.fired,
                "next_due": self.next_due, "origin": self.origin}


class RoutineModule(Module):
    name = "routines"
    rate_hz = 2.0
    priority = 58

    def __init__(self) -> None:
        super().__init__()
        self.routines: list[Routine] = []

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.routines = []
        for r in ctx.config.routines:
            self.add(Routine(r["name"], r.get("every"), r.get("at"), None, r.get("goal"), r.get("task"),
                             r.get("speak")))
        ctx.extras["routines"] = self

    def add(self, r: Routine) -> Routine:
        if r.every:
            r.next_due = self.ctx.now + r.every
        elif r.once_at is not None:
            r.next_due = r.once_at
        self.routines.append(r)
        return r

    def remove(self, name: str) -> int:
        before = len(self.routines)
        self.routines = [r for r in self.routines if r.name != name]
        return before - len(self.routines)

    def _due(self, r: Routine, now: float) -> bool:
        if r.every or r.once_at is not None:
            return now >= r.next_due
        if r.at:
            lt = time.localtime()
            today = f"{lt.tm_year}-{lt.tm_yday}"
            return f"{lt.tm_hour:02d}:{lt.tm_min:02d}" >= r.at and r.last_day != today
        return False

    async def _fire(self, r: Routine, now: float) -> None:
        r.fired += 1
        bus = self.ctx.bus
        self.ctx.safety.audit.record(now, "routines", "fire", routine=r.name)
        await bus.publish("routine/fired", r.to_dict(), source=self.name)
        if r.speak:
            await bus.publish("speech/say", {"text": r.speak, "voice": "neutral"}, source=self.name)
        if r.task:
            await bus.publish("task/start", {"name": r.task, "origin": f"routine:{r.name}"}, source=self.name)
        if r.goal:
            await bus.publish("brain/goal", {"goal": r.goal}, source=self.name)
        if r.every:
            r.next_due = now + r.every
        elif r.at:
            lt = time.localtime()
            r.last_day = f"{lt.tm_year}-{lt.tm_yday}"
        else:
            self.routines.remove(r)

    async def tick(self, dt: float) -> None:
        now = self.ctx.now
        for r in list(self.routines):
            if self._due(r, now):
                await self._fire(r, now)

    def describe(self) -> list[dict]:
        return [r.to_dict() for r in self.routines]
