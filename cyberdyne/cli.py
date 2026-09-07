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
import os
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
        in_docker = os.path.exists("/.dockerenv")
        cfg.dashboard.host = getattr(args, "host", None) or ("0.0.0.0" if in_docker else cfg.dashboard.host)
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
        sp.add_argument("--host", default=None, help="dashboard bind address (default 127.0.0.1; 0.0.0.0 in Docker)")

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

    sk = sub.add_parser("skills", help="list built-in skills")
    sk.add_subparsers(dest="skcmd").add_parser("list")

    sn = sub.add_parser("skill", help="skill tools")
    snp = sn.add_subparsers(dest="skillcmd", required=True).add_parser("new", help="scaffold a skill + test")
    snp.add_argument("name")
    snp.add_argument("--description", default="a custom skill")
    snp.add_argument("--root", default=".")

    bn = sub.add_parser("bench", help="run every scenario headless and print a metrics table")
    bn.add_argument("--seconds", type=float, default=120.0)
    bn.add_argument("names", nargs="*")

    sub.add_parser("verify", help="exhaustively check the safety gate and state machine invariants")

    rp = sub.add_parser("replay", help="inspect a bus recording (kernel.record = path)")
    rp.add_argument("file")
    rp.add_argument("--at", type=float, default=None, help="show the robot's knowledge at this sim time")
    rp.add_argument("--between", nargs=2, type=float, metavar=("T0", "T1"))
    rp.add_argument("--topic", default=None, help="glob filter for --between")

    fl = sub.add_parser("fleet", help="run several robots in lock-step simulation")
    fl.add_argument("--scenario", "-s", default="patrol")
    fl.add_argument("--robots", "-n", type=int, default=3)
    fl.add_argument("--duration", "-t", type=float, default=120.0)
    fl.add_argument("--auction", default=None, help="offer this goto target (x,y) to the fleet at t=5")

    ev = sub.add_parser("eval-llm", help="score the LLM interpreter/planner on fixed cases (needs API credentials)")
    ev.add_argument("--backend", default="anthropic", choices=["anthropic", "scripted"])
    ev.add_argument("--model", default="claude-opus-5")
    ev.add_argument("--effort", default="high")

    tr = sub.add_parser("train", help="tune the local controller in simulation (shielded by the safety gate)")
    tr.add_argument("--scenario", "-s", default="patrol")
    tr.add_argument("--episodes", "-n", type=int, default=6)
    tr.add_argument("--seconds", type=float, default=90.0, help="sim seconds per episode")

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

    if args.cmd == "skill":
        from pathlib import Path

        from .skills.scaffold import scaffold
        for path in scaffold(args.name, Path(args.root), args.description):
            print(path)
        return 0
    if args.cmd == "bench":
        from .bench import main as bench_main
        return bench_main(args.seconds, args.names or None)
    if args.cmd == "eval-llm":
        from .cognition.eval_llm import main as eval_main
        rep = eval_main(args.backend, args.model, args.effort)
        print(json.dumps(rep, indent=2, default=str))
        return 0 if rep["summary"]["accuracy"] >= 0.8 else 1
    if args.cmd == "verify":
        from .safety.verify import verify_all
        res = verify_all()
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1
    if args.cmd == "replay":
        from .observability.recording import Recording
        rec = Recording.load(args.file)
        if args.at is not None:
            print(json.dumps(rec.state_at(args.at), indent=1, default=str))
        elif args.between:
            for m in rec.between(args.between[0], args.between[1], args.topic):
                print(f"{m['ts']:8.3f} {m['topic']:<28} {json.dumps(m['payload'], default=str)[:100]}")
        else:
            print(json.dumps(rec.summary(), indent=2))
        return 0
    if args.cmd == "fleet":
        import copy

        from .fleet.sim import FleetSim
        base = load_scenario(args.scenario)
        cfgs = []
        for i in range(args.robots):
            c = copy.deepcopy(base)
            c.name = f"{base.name}-{i + 1}"
            c.dashboard.enabled = False
            c.world.robot_start = {"x": 1.0 + i * 1.2, "y": 1.0, "theta": 0.0}
            cfgs.append(c)

        async def run_fleet():
            fs = FleetSim(cfgs)
            await fs.boot()
            if args.auction:
                x, y = (float(v) for v in args.auction.split(","))
                await fs.run(5)
                await fs.robots[0].bus.publish("fleet/auction", {"name": "offered", "steps": [
                    {"kind": "goto", "args": {"x": x, "y": y, "name": "offered"}}]}, source="cli")
                await fs.run(max(0.0, args.duration - 5))
            else:
                await fs.run(args.duration)
            await fs.shutdown()
            return fs.report()
        print(json.dumps(asyncio.run(run_fleet()), indent=2, default=str))
        return 0
    if args.cmd == "train":
        from .learning.tuner import ControllerTuner
        cfg = load_scenario(args.scenario)
        cfg.dashboard.enabled = False
        res = asyncio.run(ControllerTuner(cfg, args.seconds).tune(args.episodes))
        print(json.dumps({"result": res.to_dict(), "history": res.history}, indent=2))
        print("\n# put into your scenario:\n[motion]\n" + "\n".join(f"{k} = {v}" for k, v in res.best.items()),
              file=sys.stderr)
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
