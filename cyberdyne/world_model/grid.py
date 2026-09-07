"""Log-odds occupancy grid built from range scans."""
from __future__ import annotations

import math


class OccupancyGrid:
    def __init__(self, width: float, height: float, resolution: float = 0.25) -> None:
        self.width, self.height, self.res = width, height, resolution
        self.cols = int(math.ceil(width / resolution))
        self.rows = int(math.ceil(height / resolution))
        self.cells = [0.0] * (self.cols * self.rows)     # log-odds
        self.updates = 0
        self.l_occ, self.l_free, self.clamp = 0.85, -0.4, 5.0

    def idx(self, x: float, y: float) -> int | None:
        c, r = int(x / self.res), int(y / self.res)
        if 0 <= c < self.cols and 0 <= r < self.rows:
            return r * self.cols + c
        return None

    def _bump(self, x: float, y: float, delta: float) -> None:
        i = self.idx(x, y)
        if i is not None:
            self.cells[i] = max(-self.clamp, min(self.clamp, self.cells[i] + delta))

    def integrate_scan(self, pose: dict, scan: list[dict], max_range: float) -> None:
        self.updates += 1
        px, py, th = pose["x"], pose["y"], pose["theta"]
        for p in scan:
            a = th + p["angle"]
            d = p["distance"]
            steps = max(1, int(d / (self.res * 0.5)))
            for s in range(steps):
                t = d * s / steps
                self._bump(px + t * math.cos(a), py + t * math.sin(a), self.l_free)
            if d < max_range - 1e-6:
                self._bump(px + d * math.cos(a), py + d * math.sin(a), self.l_occ)

    def probability(self, x: float, y: float) -> float:
        i = self.idx(x, y)
        if i is None:
            return 1.0
        return 1.0 - 1.0 / (1.0 + math.exp(self.cells[i]))

    def occupied_cells(self, threshold: float = 0.65) -> list[tuple[int, int]]:
        out = []
        for r in range(self.rows):
            for c in range(self.cols):
                lo = self.cells[r * self.cols + c]
                if 1.0 - 1.0 / (1.0 + math.exp(lo)) >= threshold:
                    out.append((c, r))
        return out

    def explored_fraction(self) -> float:
        known = sum(1 for v in self.cells if abs(v) > 0.3)
        return known / len(self.cells)

    def to_dict(self, threshold: float = 0.65) -> dict:
        return {"cols": self.cols, "rows": self.rows, "res": self.res,
                "occupied": self.occupied_cells(threshold),
                "explored": round(self.explored_fraction(), 3), "updates": self.updates}
