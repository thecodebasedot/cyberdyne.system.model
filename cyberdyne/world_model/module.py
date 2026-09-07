"""WorldModel module: fuses odometry + scans into the grid and entity store.

    world/summary  {explored, entities, updates}
"""
from __future__ import annotations

from ..hal.interfaces import DeviceKind, RangeSensor
from ..kernel.context import Context
from ..kernel.module import Module
from .entities import EntityStore
from .grid import OccupancyGrid


class WorldModel(Module):
    name = "world_model"
    rate_hz = 5.0
    priority = 30

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        w = ctx.config.world
        self.grid = OccupancyGrid(w.width, w.height)
        self.entities = EntityStore()
        self.max_range = ctx.devices.get(DeviceKind.RANGE, RangeSensor).max_range
        self.entities.observe("charger", "place", w.charger["x"], w.charger["y"], ctx.now)
        for i, p in enumerate(ctx.config.brain.patrol):
            self.entities.observe(f"waypoint_{i}", "waypoint", p["x"], p["y"], ctx.now, index=i)

    async def tick(self, dt: float) -> None:
        bus = self.ctx.bus
        pose = bus.latest_payload("sensor/odometry")
        scan = bus.latest_payload("perception/scan")
        if pose and scan:
            self.grid.integrate_scan(pose, scan, self.max_range)
            self.entities.observe("self", "robot", pose["x"], pose["y"], self.ctx.now)
        await bus.publish("world/summary", {"explored": round(self.grid.explored_fraction(), 3),
                                            "entities": len(self.entities.all()),
                                            "grid_updates": self.grid.updates}, source=self.name)

    def snapshot(self) -> dict:
        return {"grid": self.grid.to_dict(), "entities": self.entities.all()}
