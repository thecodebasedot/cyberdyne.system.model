# Cyberdyne System Model

A layered cognitive robotics platform, built from the kernel up.

Phase 1 ships a complete, working robot in simulation: a deterministic
kernel, a hardware abstraction layer, an immutable safety core with a
hash-chained audit log, perception, an occupancy-grid world model, memory,
a behaviour-tree brain, A* navigation, a permissioned skill system, a
command interpreter (English + romanised Bangla) and a live web dashboard.
Every later layer of the design (LLM cognition, real hardware, learning,
fleets, formal verification) plugs into contracts that already exist.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/ROADMAP.md](docs/ROADMAP.md).

```
kernel ─ hal ─ sim ─ safety ─ perception ─ world_model ─ memory ─ cognition ─ motion ─ skills ─ language ─ observability
```

## Quick start

Python 3.11+, no runtime dependencies.

```bash
pip install -e ".[dev]"

cyberdyne check                       # boot, self-test, 1 s of sim, JSON report
cyberdyne scenario list               # patrol, estop_drill, voice, sensor_fault
cyberdyne run -s patrol -t 300        # 300 sim-seconds as fast as possible
cyberdyne run -s patrol -d            # real-time with the dashboard on http://127.0.0.1:8080
cyberdyne say "go to 8 2"             # one-shot command, 20 s run, report
cyberdyne skills                      # built-in skills and their permission tiers
```

Without installing: `PYTHONPATH=. python -m cyberdyne ...`

## The dashboard

`cyberdyne run -s patrol -d` opens a control room: map with scan rays, planned
path, keep-out zones and the occupancy grid the robot has built; battery,
pose, brain mode and active behaviour-tree branch; per-module health from
the scheduler; the event stream; and the tail of the audit chain. Click the
map to send a goal, type a command (`charge koro`, `status`, `stop`), or hit
E-STOP / RESET.

## What the robot does today

* Boots through `BOOT → DIAGNOSTIC → IDLE`; a failed device self-test boots
  into `ESTOP`.
* Patrols waypoints, avoids obstacles it has never seen (reactive) and
  routes around ones it has mapped (A*), slides along walls instead of
  freezing, and backs off when stuck.
* Breaks off patrol when the battery is low, docks, charges to full, resumes.
* Stops instantly on e-stop (voice, dashboard, scripted, or a critical
  module fault); reset needs confirmation; the state machine and audit log
  record it.
* Refuses to drive forward when perception goes quiet; the watchdog
  restarts the failed module and the robot carries on.
* Takes commands in English or romanised Bangla, runs them as permissioned
  skills, and remembers what happened (episodic + working memory).

## Layout

```
cyberdyne/
  kernel/         clock, bus, module, scheduler, watchdog, state machine, config
  hal/            device interfaces, registry, virtual/ backend
  sim/            2D world, scenario loader + scripted events
  safety/         envelope, e-stop, permissions, audit log, SafetyGate
  perception/     sensor hub, range perception
  world_model/    occupancy grid, entity store
  memory/         working + episodic memory
  cognition/      behaviour tree, planner, brain
  motion/         A* grid planner, local controller
  skills/         manifests, registry, runner, builtin/
  language/       rule interpreter, language module
  observability/  telemetry, dashboard (+ dashboard.html)
  learning/       reserved (Phase 4)
  fleet/          reserved (Phase 5)
  runtime.py      wires everything from a RobotConfig
  cli.py
scenarios/        *.toml scenarios
tests/            unit + end-to-end
docs/             ARCHITECTURE.md, ROADMAP.md
```

## Writing a skill

```python
from cyberdyne.skills import Skill, SkillManifest, SkillResult

class WaveSkill(Skill):
    manifest = SkillManifest("wave", description="Wave at someone", args={"who": "name"})

    async def run(self, ctx, args):
        await ctx.bus.publish("gesture/wave", {"who": args.get("who")}, source="skill.wave")
        return SkillResult(True, f"waved at {args.get('who', 'everyone')}")
```

Add the module path to the scenario (`skills = ["mypkg.skills"]`). The
permission tier defaults to `skill.wave → LOG`; override it under
`[safety.permissions]`.

## Writing a scenario

```toml
name = "kitchen"
[world]
obstacles = [ { x = 3, y = 3, w = 1.5, h = 1, name = "table" } ]
[brain]
patrol = [ { x = 8, y = 1 }, { x = 8, y = 8 } ]
[[events]]
at = 5.0
topic = "language/utterance"
payload = { text = "jao 2 7" }
```

## Development

```bash
pytest          # 24 tests, ~4 s
ruff check .
```

## License

Eclipse Public License 2.0.
