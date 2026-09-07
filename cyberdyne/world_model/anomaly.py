"""Anomaly detection over the entity store.

    displaced   an object seen >= ``min_sightings`` times turns up ``displacement`` m from its usual spot
    new_object  an object first seen after the robot has been running a while
    unusual_hour a known person seen in a time bucket they were never seen in before (after enough history)
"""
from __future__ import annotations

import math
from collections import defaultdict


class AnomalyDetector:
    def __init__(self, displacement: float = 1.5, min_sightings: int = 3, settle_time: float = 60.0) -> None:
        self.displacement, self.min_sightings, self.settle_time = displacement, min_sightings, settle_time
        self._usual: dict[str, tuple[float, float, int]] = {}      # id -> running mean x, y, n
        self._hours: dict[str, set[str]] = defaultdict(set)
        self._flagged: dict[str, float] = {}
        self.count = 0

    def observe(self, entity: dict, now: float, hour_bucket: str) -> list[dict]:
        out = []
        eid, kind = entity["id"], entity["kind"]
        if kind == "object":
            mx, my, n = self._usual.get(eid, (entity["x"], entity["y"], 0))
            if n >= self.min_sightings and math.hypot(entity["x"] - mx, entity["y"] - my) > self.displacement:
                if now - self._flagged.get(eid, -1e9) > 30:
                    self._flagged[eid] = now
                    detail = f"usually at ({mx:.1f}, {my:.1f}), now at ({entity['x']:.1f}, {entity['y']:.1f})"
                    out.append({"id": eid, "kind": "displaced", "detail": detail})
                self._usual[eid] = (entity["x"], entity["y"], 1)          # accept the new normal
            else:
                n2 = n + 1
                self._usual[eid] = ((mx * n + entity["x"]) / n2, (my * n + entity["y"]) / n2, n2)
            if n == 0 and now > self.settle_time and entity["seen_count"] <= 1:
                out.append({"id": eid, "kind": "new_object", "detail": f"first seen at t={now:.0f}s"})
        elif kind == "person" and ":" in eid:
            seen = self._hours[eid]
            if len(seen) >= 2 and hour_bucket not in seen and now - self._flagged.get(eid, -1e9) > 300:
                self._flagged[eid] = now
                out.append({"id": eid, "kind": "unusual_hour", "detail": f"never seen in the {hour_bucket} before"})
            seen.add(hour_bucket)
        self.count += len(out)
        return out
