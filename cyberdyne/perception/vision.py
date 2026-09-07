"""VisionPerception: camera frames -> persistent tracks in world coordinates.

    perception/detections  [{kind,label,bearing,distance,confidence,signature}]
    perception/tracks      [{track_id,kind,x,y,confidence,signature,age,last_seen}]
    perception/people      [{x,y,track_id}]   (for social navigation)

The tracker is nearest-neighbour association in world coordinates with a
gating radius; tracks that are not seen for ``track_ttl`` seconds expire.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..hal.interfaces import Camera, Detection, DeviceKind
from ..kernel.context import Context
from ..kernel.module import Module


@dataclass
class Track:
    track_id: int
    kind: str
    x: float
    y: float
    confidence: float
    signature: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    hits: int = 1
    history: list[tuple[float, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"track_id": self.track_id, "kind": self.kind, "x": round(self.x, 2), "y": round(self.y, 2),
                "confidence": self.confidence, "signature": self.signature, "hits": self.hits,
                "first_seen": self.first_seen, "last_seen": self.last_seen}


class EntityTracker:
    def __init__(self, gate: float = 0.8, ttl: float = 3.0, smoothing: float = 0.5) -> None:
        self.gate, self.ttl, self.smoothing = gate, ttl, smoothing
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def update(self, dets: list[Detection], pose: dict, now: float) -> list[Track]:
        unmatched = set(self.tracks)
        for d in dets:
            wx = pose["x"] + d.distance * math.cos(pose["theta"] + d.bearing)
            wy = pose["y"] + d.distance * math.sin(pose["theta"] + d.bearing)
            best, best_d = None, self.gate
            for tid in unmatched:
                t = self.tracks[tid]
                if t.kind != d.kind:
                    continue
                if d.signature and t.signature and d.signature != t.signature:
                    continue
                dist = math.hypot(t.x - wx, t.y - wy)
                if dist < best_d:
                    best, best_d = tid, dist
            if best is None:
                t = Track(self._next, d.kind, wx, wy, d.confidence, d.signature, now, now)
                self.tracks[self._next] = t
                self._next += 1
            else:
                t = self.tracks[best]
                unmatched.discard(best)
                a = self.smoothing
                t.x, t.y = a * wx + (1 - a) * t.x, a * wy + (1 - a) * t.y
                t.confidence = max(t.confidence * 0.7, d.confidence)
                t.signature = t.signature or d.signature
                t.last_seen, t.hits = now, t.hits + 1
            t.history.append((t.x, t.y))
            if len(t.history) > 50:
                t.history.pop(0)
        for tid in list(self.tracks):
            if now - self.tracks[tid].last_seen > self.ttl:
                del self.tracks[tid]
        return list(self.tracks.values())


class VisionPerception(Module):
    name = "vision"
    rate_hz = 10.0
    priority = 22

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.camera = ctx.devices.get(DeviceKind.CAMERA, Camera)
        self.tracker = EntityTracker()
        self.frames = 0

    async def tick(self, dt: float) -> None:
        pose = self.ctx.bus.latest_payload("sensor/odometry")
        if pose is None:
            return
        frame = await self.camera.capture()
        self.frames += 1
        tracks = self.tracker.update(frame.detections, pose, self.ctx.now)
        bus = self.ctx.bus
        await bus.publish("perception/detections", [d.to_dict() for d in frame.detections], source=self.name)
        await bus.publish("perception/tracks", [t.to_dict() for t in tracks], source=self.name)
        await bus.publish("perception/people", [{"x": t.x, "y": t.y, "track_id": t.track_id}
                                                for t in tracks if t.kind == "person"], source=self.name)
