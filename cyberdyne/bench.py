"""Benchmark suite: run every scenario headless and report the numbers that matter."""
from __future__ import annotations

import asyncio
import time

from .runtime import Runtime
from .sim.scenario import list_scenarios, load_scenario

COLUMNS = ("scenario", "sim_s", "wall_s", "laps", "distance_m", "collisions", "contacts", "recoveries",
           "questions", "tasks_done", "faults", "audit_ok")


async def run_one(name: str, seconds: float) -> dict:
    cfg = load_scenario(name)
    cfg.dashboard.enabled = False
    rt = Runtime(cfg)
    counts = {"recoveries": 0, "questions": 0}
    rt.bus.subscribe("nav/recovery", lambda m: counts.__setitem__("recoveries", counts["recoveries"] + 1))
    rt.bus.subscribe("brain/question", lambda m: counts.__setitem__("questions", counts["questions"] + 1))
    t0 = time.perf_counter()
    await rt.boot()
    await rt.run(seconds)
    await rt.shutdown()
    r = rt.report()
    return {"scenario": name, "sim_s": seconds, "wall_s": round(time.perf_counter() - t0, 2),
            "laps": (r["brain"] or {}).get("laps", 0), "distance_m": round(r["world"]["distance"], 1),
            "collisions": r["world"]["collisions"], "contacts": r["world"]["contacts"],
            "recoveries": counts["recoveries"], "questions": counts["questions"],
            "tasks_done": r["tasks"]["completed"], "faults": r["scheduler"]["faults"],
            "audit_ok": r["audit"]["chain_ok"]}


async def run_all(seconds: float = 120.0, names: list[str] | None = None) -> list[dict]:
    names = names or [p.stem for p in list_scenarios()]
    return [await run_one(n, seconds) for n in names]


def table(rows: list[dict]) -> str:
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in COLUMNS}
    line = " | ".join(c.ljust(widths[c]) for c in COLUMNS)
    sep = "-+-".join("-" * widths[c] for c in COLUMNS)
    body = "\n".join(" | ".join(str(r[c]).ljust(widths[c]) for c in COLUMNS) for r in rows)
    return f"{line}\n{sep}\n{body}"


def main(seconds: float = 120.0, names: list[str] | None = None) -> int:
    rows = asyncio.run(run_all(seconds, names))
    print(table(rows))
    bad = [r for r in rows if r["collisions"] > 0 or r["faults"] > 0 or not r["audit_ok"]]
    return 1 if bad else 0
