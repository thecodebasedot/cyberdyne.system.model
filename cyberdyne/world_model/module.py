"""WorldModel module: fuses odometry + scans into the grid and entity store.

    in : sensor/odometry, perception/scan, world/observe {id, kind, x, y, attrs}
    out: world/summary  {explored, entities, updates, room}
         world/room     {name}            when the robot changes room

Rooms are named rectangles from config; every entity carries the room it
was last seen in, which is what makes "where are my keys?" answerable.
"""
from __future__ import annotations

from ..hal.interfaces import DeviceKind, RangeSensor
from ..kernel.context import Context
from ..kernel.module import Module
from ..sim.world import Obstacle
from .anomaly import AnomalyDetector
from .entities import EntityStore
from .grid import OccupancyGrid
from .scene import describe, relations


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
        self.rooms = [Obstacle(r["x"], r["y"], r["w"], r["h"], r["name"]) for r in w.rooms]
        self.current_room: str | None = None
        self.entities.observe("charger", "place", w.charger["x"], w.charger["y"], ctx.now,
                              room=self.room_of(w.charger["x"], w.charger["y"]))
        for r in self.rooms:
            self.entities.observe(r.name, "room", r.x + r.w / 2, r.y + r.h / 2, ctx.now, room=r.name)
        for i, p in enumerate(ctx.config.brain.patrol):
            self.entities.observe(f"waypoint_{i}", "waypoint", p["x"], p["y"], ctx.now, index=i,
                                  room=self.room_of(p["x"], p["y"]))
        self._sub = ctx.bus.subscribe("world/observe", self._on_observe, name="world_model.observe")
        self.anomalies = AnomalyDetector(ctx.config.perception.anomaly_displacement)
        self.hour_fn = lambda: __import__("time").localtime().tm_hour
        self._scene_due = 0.0
        self.scene: dict = {"room": None, "relations": [], "summary": ""}
        self._pending_anomalies: list[dict] = []

    async def teardown(self) -> None:
        self.ctx.bus.unsubscribe(self._sub)

    def room_of(self, x: float, y: float) -> str | None:
        for r in self.rooms:
            if r.contains(x, y):
                return r.name
        return None

    def _on_observe(self, msg) -> None:
        p = msg.payload
        e = self.entities.observe(p["id"], p["kind"], p["x"], p["y"], self.ctx.now,
                                  room=self.room_of(p["x"], p["y"]), **(p.get("attrs") or {}))
        bucket = {0: "night", 1: "morning", 2: "afternoon", 3: "evening"}[min(3, self.hour_fn() // 6)]
        self._pending_anomalies += self.anomalies.observe(e.to_dict(), self.ctx.now, bucket)

    def places(self) -> dict[str, tuple[float, float]]:
        """Name -> coordinates for everything the planner may target by name."""
        out: dict[str, tuple[float, float]] = {}
        for e in self.entities.all():
            key = e["id"].split(":", 1)[-1].lower()
            out[key] = (e["x"], e["y"])
        return out

    def find(self, name: str) -> dict | None:
        n = name.lower().strip()
        for e in self.entities.all():
            eid = e["id"].lower()
            if eid == n or eid.split(":", 1)[-1] == n or eid.split("#", 1)[0] == n or n in e["attrs"].values():
                return e
        return None

    async def tick(self, dt: float) -> None:
        bus = self.ctx.bus
        pose = bus.latest_payload("sensor/odometry")
        scan = bus.latest_payload("perception/scan")
        if pose and scan:
            self.grid.integrate_scan(pose, scan, self.max_range)
            room = self.room_of(pose["x"], pose["y"])
            self.entities.observe("self", "robot", pose["x"], pose["y"], self.ctx.now, room=room)
            if room != self.current_room:
                self.current_room = room
                await bus.publish("world/room", {"name": room}, source=self.name)
        for a in self._pending_anomalies:
            self.ctx.safety.audit.record(self.ctx.now, "world", f"anomaly.{a['kind']}", id=a["id"], detail=a["detail"])
            await bus.publish("world/anomaly", a, source=self.name)
        self._pending_anomalies.clear()
        if self.ctx.now >= self._scene_due:
            self._scene_due = self.ctx.now + 1.0
            rel = relations(self.entities.all(), pose, now=self.ctx.now)
            self.scene = {"room": self.current_room, "relations": [list(r) for r in rel],
                          "summary": describe(rel, self.current_room)}
            await bus.publish("world/scene", self.scene, source=self.name)
        await bus.publish("world/summary", {"explored": round(self.grid.explored_fraction(), 3),
                                            "entities": len(self.entities.all()),
                                            "grid_updates": self.grid.updates,
                                            "room": self.current_room}, source=self.name)

    def snapshot(self) -> dict:
        return {"grid": self.grid.to_dict(), "entities": self.entities.all(),
                "rooms": [r.to_dict() for r in self.rooms], "room": self.current_room, "scene": self.scene}
