"""``cyberdyne`` command line.

    cyberdyne run [--scenario NAME|PATH] [--duration S] [--realtime F] [--dashboard [PORT]]
    cyberdyne scenario list
    cyberdyne check            # boot, self-test, one second of sim, report
    cyberdyne skills
    cyberdyne say "go to 6 7"  # one-shot: boot, send utterance, run 20 s, report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from . import __version__
from .kernel.config import RobotConfig
from .runtime import Runtime
from .sim.scenario import list_scenarios, load_scenario
from .skills.registry import SkillRegistry


def _config(args) -> RobotConfig:
    cfg = load_scenario(args.scenario) if getattr(args, "scenario", None) else RobotConfig()
    if getattr(args, "realtime", None) is not None:
        cfg.kernel.realtime_factor = args.realtime
    if getattr(args, "dashboard", None) is not None:
        cfg.dashboard.enabled = True
        if args.dashboard:
            cfg.dashboard.port = args.dashboard
    return cfg


async def _run(cfg: RobotConfig, duration: float | None, utterances: list[str] = ()) -> dict:
    rt = Runtime(cfg)
    await rt.boot()
    if rt.dashboard:
        print(f"dashboard: {rt.dashboard.url}", file=sys.stderr)
    for u in utterances:
        await rt.say(u)
    try:
        await rt.run(duration)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        await rt.shutdown()
    return rt.report()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cyberdyne", description="Cyberdyne System Model")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--scenario", "-s", help="scenario name (scenarios/*.toml) or path")
        sp.add_argument("--realtime", "-r", type=float, default=None,
                        help="real-time factor (1.0 = wall clock); default: as fast as possible")
        sp.add_argument("--dashboard", "-d", nargs="?", const=0, type=int, metavar="PORT",
                        help="serve the web dashboard (default port 8080)")

    r = sub.add_parser("run", help="run the robot")
    common(r)
    r.add_argument("--duration", "-t", type=float, default=None, help="sim seconds; default: forever")

    c = sub.add_parser("check", help="boot + self-test + short run, print a report")
    common(c)

    s = sub.add_parser("say", help="send a command and run for a while")
    common(s)
    s.add_argument("text", nargs="+")
    s.add_argument("--duration", "-t", type=float, default=20.0)

    sc = sub.add_parser("scenario", help="scenario tools")
    sc.add_subparsers(dest="scmd", required=True).add_parser("list")

    sub.add_parser("skills", help="list built-in skills")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "scenario":
        for path in list_scenarios():
            print(path.stem)
        return 0
    if args.cmd == "skills":
        reg = SkillRegistry()
        reg.load_builtin()
        for m in reg.describe():
            print(f"{m['name']:<12} {m['permission']:<20} {m['description']}")
        return 0

    cfg = _config(args)
    if args.cmd == "run":
        if cfg.dashboard.enabled and cfg.kernel.realtime_factor is None:
            cfg.kernel.realtime_factor = 1.0      # a dashboard is for humans
        report = asyncio.run(_run(cfg, args.duration))
    elif args.cmd == "check":
        report = asyncio.run(_run(cfg, 1.0))
    else:
        report = asyncio.run(_run(cfg, args.duration, [" ".join(args.text)]))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["audit"]["chain_ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
