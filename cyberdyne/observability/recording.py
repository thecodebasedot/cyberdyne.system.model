"""Recording and time-travel.

``BusRecorder`` taps the bus and writes every message as one JSON line.
``Recording`` loads a file and answers "what did the robot know at time T"
(latest payload per topic at T) and "what happened between T1 and T2".
Because the kernel is deterministic in simulation, the same scenario yields
the same recording, which ``digest()`` turns into one comparable hash.
"""
from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from pathlib import Path
from typing import Any

from ..kernel.bus import Message, MessageBus

_EXCLUDE_FROM_DIGEST = ("telemetry/", "kernel/diagnostic")


class BusRecorder:
    def __init__(self, bus: MessageBus, path: str | Path, exclude: tuple[str, ...] = ("telemetry/",)) -> None:
        self.bus = bus
        self.path = Path(path)
        self.exclude = exclude
        self._fh = self.path.open("w", encoding="utf-8")
        self.count = 0
        bus.taps.append(self._tap)

    def _tap(self, msg: Message) -> None:
        if msg.topic.startswith(self.exclude):
            return
        self._fh.write(json.dumps(msg.to_dict(), default=str, sort_keys=True) + "\n")
        self.count += 1

    def close(self) -> None:
        if self._tap in self.bus.taps:
            self.bus.taps.remove(self._tap)
        self._fh.close()


class Recording:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = sorted(messages, key=lambda m: (m["ts"], m["seq"]))
        self._ts = [m["ts"] for m in self.messages]

    @classmethod
    def load(cls, path: str | Path) -> Recording:
        with Path(path).open(encoding="utf-8") as fh:
            return cls([json.loads(line) for line in fh if line.strip()])

    @property
    def duration(self) -> float:
        return self._ts[-1] if self._ts else 0.0

    def topics(self) -> list[str]:
        return sorted({m["topic"] for m in self.messages})

    def between(self, t0: float, t1: float, pattern: str | None = None) -> list[dict]:
        import fnmatch
        lo, hi = bisect_right(self._ts, t0 - 1e-9), bisect_right(self._ts, t1)
        out = self.messages[lo:hi]
        return [m for m in out if pattern is None or fnmatch.fnmatchcase(m["topic"], pattern)]

    def state_at(self, t: float) -> dict[str, Any]:
        """Latest payload per topic as of time ``t`` (what every module could see)."""
        state: dict[str, Any] = {}
        for m in self.messages[:bisect_right(self._ts, t)]:
            state[m["topic"]] = m["payload"]
        return state

    def digest(self) -> str:
        h = hashlib.sha256()
        for m in self.messages:
            if m["topic"].startswith(_EXCLUDE_FROM_DIGEST):
                continue
            h.update(json.dumps([round(m["ts"], 6), m["topic"], m["source"], m["payload"]],
                                sort_keys=True, default=str).encode())
        return h.hexdigest()

    def summary(self) -> dict:
        per_topic: dict[str, int] = {}
        for m in self.messages:
            per_topic[m["topic"]] = per_topic.get(m["topic"], 0) + 1
        return {"messages": len(self.messages), "duration": round(self.duration, 3),
                "topics": len(per_topic), "digest": self.digest()[:16],
                "top": sorted(per_topic.items(), key=lambda kv: -kv[1])[:8]}
