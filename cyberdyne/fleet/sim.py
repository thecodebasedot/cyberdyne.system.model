"""FleetSim: several robots, one clock, lock-step frames.

Each robot is a full ``Runtime`` with its own world (or a shared one), bus
and scheduler; they only see each other through the fleet transport. The
harness advances one shared ``SimClock`` and runs every scheduler's due
modules per frame, so the whole fleet stays deterministic.
"""
from __future__ import annotations

from ..kernel.clock import SimClock
from ..kernel.config import RobotConfig
from ..runtime import Runtime
from .auction import AuctionModule
from .bridge import FleetBridge
from .transport import InMemoryTransport


class FleetSim:
    def __init__(self, configs: list[RobotConfig], realtime_factor: float | None = None) -> None:
        self.clock = SimClock(realtime_factor=realtime_factor)
        self.hub = InMemoryTransport.Hub()
        self.robots: list[Runtime] = []
        for cfg in configs:
            rt = Runtime(cfg, clock=self.clock)
            bridge = FleetBridge(InMemoryTransport(self.hub, cfg.name), cfg.name)
            rt.scheduler.register(bridge, AuctionModule(cfg.name))
            self.robots.append(rt)

    async def boot(self) -> None:
        for rt in self.robots:
            await rt.boot()

    async def run(self, duration: float) -> None:
        end = self.clock.now() + duration
        while self.clock.now() < end:
            now = self.clock.now()
            for rt in self.robots:
                await rt.scheduler.frame(now)
            nxt = min(rt.scheduler.next_due(now + 0.1) for rt in self.robots)
            await self.clock.sleep_until(min(max(nxt, now + 1e-6), end))

    async def shutdown(self) -> None:
        for rt in self.robots:
            await rt.shutdown()

    def report(self) -> dict:
        return {rt.config.name: {**{k: rt.report()[k] for k in ("state", "battery", "pose", "tasks")},
                                 "fleet": rt.ctx.extras["fleet"].describe(),
                                 "auction": rt.ctx.extras["auction"].describe()} for rt in self.robots}

    def get(self, name: str) -> Runtime:
        return next(rt for rt in self.robots if rt.config.name == name)
