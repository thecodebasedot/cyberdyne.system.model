"""RangePerception: raw scan -> structured obstacle observations.

    perception/scan             [{angle, distance}, ...]
    perception/front_clearance  float  (min distance within +-30 deg)
    perception/obstacles        [{angle, distance}] closest hit per 45-degree sector
"""
from __future__ import annotations

import math

from ..hal.interfaces import DeviceKind, RangeSensor
from ..kernel.context import Context
from ..kernel.module import Module


class RangePerception(Module):
    name = "perception"
    rate_hz = 15.0
    priority = 20
    front_half_angle = math.radians(30)

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.sensor = ctx.devices.get(DeviceKind.RANGE, RangeSensor)
        self.last_scan: list[dict] = []

    async def tick(self, dt: float) -> None:
        scan = await self.sensor.scan()
        self.last_scan = [{"angle": p.angle, "distance": p.distance} for p in scan]
        front = [p.distance for p in scan if abs(p.angle) <= self.front_half_angle]
        clearance = min(front) if front else self.sensor.max_range
        sectors: dict[int, dict] = {}
        for p in scan:
            k = int(round(p.angle / (math.pi / 4)))
            if p.distance < self.sensor.max_range and (k not in sectors or p.distance < sectors[k]["distance"]):
                sectors[k] = {"angle": p.angle, "distance": p.distance}
        bus = self.ctx.bus
        await bus.publish("perception/scan", self.last_scan, source=self.name)
        await bus.publish("perception/front_clearance", clearance, source=self.name)
        await bus.publish("perception/obstacles", list(sectors.values()), source=self.name)
