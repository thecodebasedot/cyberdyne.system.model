"""Scenario files: a RobotConfig plus a timeline of scripted events.

An event is ``{"at": <sim seconds>, "topic": "...", "payload": {...}}`` and is
published on the bus when the clock reaches ``at``. That is enough to script
e-stops, injected faults, goals and user commands for regression tests.
"""
from __future__ import annotations

from pathlib import Path

from ..kernel.config import RobotConfig, load_config
from ..kernel.context import Context
from ..kernel.module import Module

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios"


def list_scenarios(directory: Path = SCENARIO_DIR) -> list[Path]:
    return sorted(p for p in directory.glob("*.toml"))


def load_scenario(name_or_path: str) -> RobotConfig:
    p = Path(name_or_path)
    if not p.exists():
        p = SCENARIO_DIR / f"{name_or_path}.toml"
    if not p.exists():
        raise FileNotFoundError(f"scenario not found: {name_or_path}")
    return load_config(p)


class ScenarioEvents(Module):
    """Replays the scripted event timeline from the config."""
    name = "scenario"
    rate_hz = 20.0
    priority = 5

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._pending = sorted(ctx.config.events, key=lambda e: e["at"])
        self.fired = 0

    async def tick(self, dt: float) -> None:
        now = self.ctx.now
        while self._pending and self._pending[0]["at"] <= now:
            ev = self._pending.pop(0)
            self.fired += 1
            await self.ctx.bus.publish(ev["topic"], ev.get("payload"), source="scenario")
