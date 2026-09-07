"""Scene graph: qualitative relations between what the robot knows about.

    near(a, b)            within ``near_m``
    in(a, room)
    left_of / right_of / ahead_of / behind (relative to the robot)

``describe()`` turns the graph into one sentence for speech and the LLM brief.
"""
from __future__ import annotations

import math


def relations(entities: list[dict], pose: dict | None, near_m: float = 1.0, stale_after: float = 30.0,
              now: float = 0.0) -> list[tuple[str, str, str]]:
    live = [e for e in entities
            if e["kind"] not in ("robot", "room", "waypoint") and now - e["last_seen"] <= stale_after]
    rel: list[tuple[str, str, str]] = []
    for e in live:
        if e["attrs"].get("room"):
            rel.append((e["id"], "in", e["attrs"]["room"]))
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) <= near_m:
                rel.append((a["id"], "near", b["id"]))
    if pose:
        for e in live:
            b = math.atan2(e["y"] - pose["y"], e["x"] - pose["x"]) - pose["theta"]
            b = math.atan2(math.sin(b), math.cos(b))
            side = "ahead_of" if abs(b) < math.pi / 4 else "left_of" if 0 < b < 3 * math.pi / 4 else \
                "right_of" if -3 * math.pi / 4 < b < 0 else "behind"
            rel.append((e["id"], side, "robot"))
    return rel


def describe(rel: list[tuple[str, str, str]], room: str | None) -> str:
    names = lambda i: i.split(":", 1)[-1].split("#", 1)[0]  # noqa: E731
    parts = []
    for a, r, b in rel:
        if r == "in":
            parts.append(f"{names(a)} is in the {b}")
        elif r == "near":
            parts.append(f"{names(a)} is near {names(b)}")
        elif b == "robot":
            parts.append(f"{names(a)} is {r.replace('_', ' ')} me")
    where = f"I am in the {room}. " if room else ""
    return where + (". ".join(parts[:6]) + "." if parts else "I see nothing of note.")
