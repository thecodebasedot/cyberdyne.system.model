"""Clock synchronisation across a fleet (NTP-style, no server).

Each heartbeat carries the sender's clock. From (my receive time, their
send time) samples the peer's offset is estimated with a median over a
window; the fleet-wide reference is the median of peer offsets plus zero
(my own). ``to_fleet(t)`` / ``from_fleet(t)`` convert timestamps so LWW
merges and auctions compare like with like even when robots drifted apart.
"""
from __future__ import annotations

from collections import deque
from statistics import median


class ClockSync:
    def __init__(self, window: int = 9, max_skew_warn: float = 0.5) -> None:
        self._samples: dict[str, deque] = {}
        self.window = window
        self.max_skew_warn = max_skew_warn

    def observe(self, robot: str, their_time: float, my_time: float) -> float:
        """Record one sample; returns the peer's estimated offset (their - mine)."""
        q = self._samples.setdefault(robot, deque(maxlen=self.window))
        q.append(their_time - my_time)
        return median(q)

    def offset(self, robot: str) -> float | None:
        q = self._samples.get(robot)
        return median(q) if q else None

    def fleet_offset(self) -> float:
        """Offset from my clock to the fleet reference (median of everyone incl. me at 0)."""
        offs = [median(q) for q in self._samples.values() if q] + [0.0]
        return median(offs)

    def to_fleet(self, t: float) -> float:
        return t + self.fleet_offset()

    def from_fleet(self, t: float) -> float:
        return t - self.fleet_offset()

    def skewed_peers(self) -> dict[str, float]:
        ref = self.fleet_offset()
        return {r: round(median(q) - ref, 3) for r, q in self._samples.items()
                if q and abs(median(q) - ref) > self.max_skew_warn}

    def status(self) -> dict:
        return {"fleet_offset": round(self.fleet_offset(), 4),
                "peers": {r: round(median(q), 4) for r, q in self._samples.items() if q},
                "skewed": self.skewed_peers()}
