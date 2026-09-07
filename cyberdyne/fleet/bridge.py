"""FleetBridge: presence + topic mirroring + shared-world sync.

    out over transport : heartbeat {state, pose, battery, task}; mirrored topics; entity deltas
    in  from transport : fleet/<robot>/<topic>  (mirrored)      fleet/peers [..]
                         world/observe with fleet:<robot> attrs (entity LWW merge)

Entity sync is a last-writer-wins register per entity id keyed on the
observer's timestamp: two robots seeing the same person converge on the
most recent sighting without any coordination.
"""
from __future__ import annotations

from ..kernel.context import Context
from ..kernel.module import Module
from .interfaces import FleetMessage, Transport

MIRRORED = ("security/alert", "safety/estop_state", "task/done", "task/failed", "brain/question")


class FleetBridge(Module):
    name = "fleet"
    rate_hz = 5.0
    priority = 80

    def __init__(self, transport: Transport, robot: str, heartbeat: float = 1.0, peer_timeout: float = 5.0,
                 share_entities: bool = True) -> None:
        super().__init__()
        self.transport = transport
        self.robot = robot
        self.heartbeat = heartbeat
        self.peer_timeout = peer_timeout
        self.share_entities = share_entities
        self.peers: dict[str, dict] = {}
        self._last_beat = -1e9
        self._outbox: list[FleetMessage] = []
        self._entity_clock: dict[str, float] = {}
        self.sent = self.received = 0

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._subs = [ctx.bus.subscribe(t, self._mirror, name=f"fleet.{t}") for t in MIRRORED]
        self._subs.append(ctx.bus.subscribe("fleet/send", self._on_send, name="fleet.send"))
        self._subs.append(ctx.bus.subscribe("world/observe", self._on_observe, name="fleet.observe"))
        ctx.extras["fleet"] = self

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    def _mirror(self, msg) -> None:
        self._outbox.append(FleetMessage(self.robot, msg.topic, msg.payload, msg.ts))

    def _on_send(self, msg) -> None:
        p = msg.payload or {}
        self._outbox.append(FleetMessage(self.robot, str(p["topic"]), p.get("payload"), self.ctx.now))

    def _on_observe(self, msg) -> None:
        p = msg.payload
        if not self.share_entities or (p.get("attrs") or {}).get("fleet"):
            return                                   # do not re-share what came from the fleet
        if p["kind"] == "robot":
            return
        self._entity_clock[p["id"]] = msg.ts
        self._outbox.append(FleetMessage(self.robot, "world/entity", {**p, "observed_at": msg.ts}, msg.ts))

    async def _apply_entity(self, m: FleetMessage) -> None:
        p = m.payload
        if self._entity_clock.get(p["id"], -1) >= p["observed_at"]:
            return                                   # our own sighting is newer (LWW)
        self._entity_clock[p["id"]] = p["observed_at"]
        attrs = dict(p.get("attrs") or {})
        attrs["fleet"] = m.robot
        await self.ctx.bus.publish("world/observe", {"id": p["id"], "kind": p["kind"], "x": p["x"], "y": p["y"],
                                                     "attrs": attrs}, source=self.name)

    async def tick(self, dt: float) -> None:
        bus, now = self.ctx.bus, self.ctx.now
        if now - self._last_beat >= self.heartbeat:
            self._last_beat = now
            task = (bus.latest_payload("task/status") or {}).get("task")
            self._outbox.append(FleetMessage(self.robot, "fleet/heartbeat", {
                "state": self.ctx.state.state.value, "pose": bus.latest_payload("sensor/odometry"),
                "battery": bus.latest_payload("sensor/battery"), "task": task["name"] if task else None,
                "estop": self.ctx.safety.estop.engaged}, now))
        for m in self._outbox:
            await self.transport.send(m)
            self.sent += 1
        self._outbox.clear()
        for m in await self.transport.receive():
            self.received += 1
            if m.topic == "fleet/heartbeat":
                self.peers[m.robot] = {"robot": m.robot, "last_seen": now, **(m.payload or {})}
            elif m.topic == "world/entity":
                await self._apply_entity(m)
            else:
                await bus.publish(f"fleet/{m.robot}/{m.topic}", m.payload, source=f"fleet:{m.robot}")
        for r in list(self.peers):
            if now - self.peers[r]["last_seen"] > self.peer_timeout:
                await bus.publish("fleet/peer_lost", {"robot": r}, source=self.name)
                del self.peers[r]
        await bus.publish("fleet/peers", [{"robot": p["robot"], "last_seen": p["last_seen"], "state": p.get("state"),
                                           "pose": p.get("pose"), "battery": p.get("battery")}
                                          for p in self.peers.values()], source=self.name)

    def describe(self) -> dict:
        return {"robot": self.robot, "peers": sorted(self.peers), "sent": self.sent, "received": self.received}
