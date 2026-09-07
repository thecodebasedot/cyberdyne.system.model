# Cyberdyne System Model

A layered cognitive robotics platform, built from the kernel up.

Phase 1 ships a complete, working robot in simulation: a deterministic
kernel, a hardware abstraction layer, an immutable safety core with a
hash-chained audit log, perception, an occupancy-grid world model, memory,
a behaviour-tree brain, A* navigation, a permissioned skill system, a
command interpreter (English + romanised Bangla) and a live web dashboard.

Phase 2 gives it a mind: free-form goals go through a **council** (perceiver,
planner, safety officer, critic) that reviews every plan against a
deterministic constitution, rehearses it in a mental simulation built from
the robot's own map, and asks the human when it is blocked or unsure. The
planner and interpreter can be Claude (official SDK, optional) or rules;
rules remain the fallback and the guardrail either way. Memory grows a
knowledge graph, vector search and a `forget` primitive.

Phase 3 gives it a body: a camera, microphone and speaker in the HAL; people
and objects in the simulation; vision tracking with persistent ids; face-style
identity with owner / guest / unknown trust that gates what a speaker may
ask; wake-word voice control that talks back; rooms and "where are my keys";
social navigation that gives people space; and a serial bridge to a real
microcontroller with a fake firmware for tests and a calibration routine.

Phase 4 makes it useful and lets it learn: long-horizon tasks with retries,
pause and resume; timed routines and reminders; smart-home devices by room;
routes taught by demonstration; a user model that suggests habits; a
simulation-based tuner for the local controller that the safety gate still
shields; self-authored macro skills (declarative, constitution-checked,
owner-gated); and everything learned persisted across runs.

Phase 5 scales it: typed message schemas with a strict bus, modules that run
in isolated processes and get respawned when they crash, bus recordings you
can rewind, a fleet layer (presence, shared sightings, sealed-bid task
auctions, lock-step multi-robot simulation), over-the-air behaviour bundles
with health-checked rollback, and an exhaustive verifier for the safety
core plus a TLA+ spec of the same invariants.

All fifteen layers of the original design now have a home in this tree.
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
cyberdyne scenario list               # patrol, estop_drill, voice, sensor_fault, council, home, chores
cyberdyne train -s patrol -n 6        # tune the local controller in sim; prints [motion] values
cyberdyne verify                      # exhaustive safety-gate + state-machine check
cyberdyne bench --seconds 120         # every scenario headless -> metrics table
cyberdyne skill new wave              # scaffold skills_ext/wave.py + a test using cyberdyne.testing
cyberdyne fleet -s home -n 3 --auction 9,1   # three robots, one auction
cyberdyne replay run.jsonl --at 12.5  # what did the robot know at t=12.5 (kernel.record = "run.jsonl")
cyberdyne run -s patrol -t 300        # 300 sim-seconds as fast as possible
cyberdyne run -s patrol -d            # real-time with the dashboard on http://127.0.0.1:8080
cyberdyne say "go to 8 2"             # one-shot command, 20 s run, report
cyberdyne skills                      # built-in skills and their permission tiers
```

Without installing: `PYTHONPATH=. python -m cyberdyne ...`

## Using Claude as the planner and interpreter

```bash
pip install -e ".[llm]"          # official anthropic SDK
export ANTHROPIC_API_KEY=...     # or `ant auth login`
```

In a scenario (or `RobotConfig.brain`):

```toml
[brain]
llm = "anthropic"                # "none" = rule-based (default)
model = "claude-opus-5"
effort = "high"                  # planner; the interpreter always runs at "low"
confidence_threshold = 0.6
```

The model only ever *proposes*. Every plan still passes the constitution
(bounds, keep-out zones, obstacles, permissions, known skills), a mental
simulation, and the safety gate at the motors. A transport error, refusal or
unparsable reply falls back to the rule planner and lowers confidence, which
usually means the robot asks you before acting.

```bash
cyberdyne say "ranna ghor-er janala-r kache jao"     # rules can't parse it -> Claude maps it to a goal
cyberdyne say "keno korle"                           # explain the last decision
cyberdyne eval-llm                                   # score the model on fixed Bangla/English cases
```

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
  skills, and remembers what happened (episodic + working + semantic memory).
* Sees people and objects, tracks them, greets the people it knows, raises an
  alert on strangers when armed, remembers which room it last saw things in,
  and answers "amar keys kothay".
* Listens for its wake word, knows who is talking, and refuses requests the
  speaker's trust level does not allow.
* Runs long tasks ("rounds": go to the kitchen, lights on, wait, living
  room, lights off) with retries and timeouts, pauses them to charge and
  resumes; fires routines and reminders; learns a route you demonstrate
  and replays it; suggests what you usually ask for at this hour.
* Works in a fleet: sees who else is up, shares sightings, bids on offered
  tasks and lets the closest healthy robot win.
* Takes behaviour updates over the air and rolls them back by itself if
  anything faults in the first seconds.
* Deliberates on free-form goals: refuses goals into keep-out zones or
  obstacles outright, asks before confirm-tier actions or when its own
  prediction is shaky, accepts `proceed` / `cancel` / a new goal, and can
  explain why it decided what it decided.

## Layout

```
cyberdyne/
  kernel/         clock, bus, module, scheduler, watchdog, state machine, config, schemas, isolation
  hal/            device interfaces, registry, virtual/ backend, serial/ backend, calibration
  sim/            2D world, scenario loader + scripted events
  safety/         envelope, e-stop, permissions, audit log, SafetyGate, exhaustive verifier
  perception/     sensor hub, range perception, vision + entity tracker
  world_model/    occupancy grid, entity store
  memory/         working + episodic (vector search) + semantic (knowledge graph)
  cognition/      behaviour tree, planner, brain, council, constitution, mental simulation, LLM seam
  motion/         A* grid planner, local controller
  skills/         manifests, registry, runner, builtin/ (core, home), macro authoring
  language/       rule interpreter, LLM interpreter, language module, voice module
  social/         identity registry (trust), social module
  observability/  telemetry, dashboard (+ dashboard.html), recording/replay
  learning/       user model, demonstration recorder, controller tuner
  tasks/          task model, task runner, routines
  home/           smart-home device hub (virtual / MQTT / Home Assistant)
  fleet/          transports, bridge (presence + LWW sync), auction, FleetSim
  ops/            OTA bundles with health-checked rollback
  runtime.py      wires everything from a RobotConfig
  cli.py
scenarios/        *.toml scenarios
tests/            unit + end-to-end
docs/             ARCHITECTURE.md, ROADMAP.md, HARDWARE.md, formal/SafetyGate.tla
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

## Tasks, routines and devices in a scenario

```toml
[home]
devices = [ { id = "kitchen_light", kind = "light", room = "kitchen" } ]

[[tasks]]
name = "rounds"
steps = [ { kind = "goto", args = { x = 8, y = 1.5, name = "kitchen" } },
          { kind = "skill", args = { skill = "device", args = { kind = "light", room = "kitchen", on = true } } },
          { kind = "wait", args = { seconds = 2 } } ]

[[routines]]
name = "hourly rounds"
every = 3600
task = "rounds"

[learning]
data_dir = "~/.cyberdyne"        # persist facts, habits, taught routes, macros
```

Say `start task rounds`, `remind me in 30 seconds to check the oven`,
`teach corner_run` ... `done teaching`, `kitchen er light jalao`.

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

## Physics and noise

`[kernel] mode = "bullet"` runs the same robot on PyBullet (`pip install
pybullet numpy`). `[world] odometry_noise = 0.05` makes the virtual wheels
slip; the EKF in the sensor hub fuses odometry, IMU heading and wall
landmarks so navigation still works (about 0.2 m error instead of 0.7 m
after two minutes of patrol).

## Real hardware

`mode = "serial"` talks to a microcontroller over a one-line-per-command
protocol (`VEL`, `ODOM?`, `SCAN?`, `BATT?`); `firmware/` is the Arduino
sketch, with its protocol logic compiled and tested on the host.
`mode = "rpi"` adds a USB camera (OpenCV), an offline microphone (Vosk) and
text-to-speech on a Raspberry Pi; `scripts/setup_rpi.sh` installs it all
and `deploy/cyberdyne.service` runs it at boot. `port = "loopback"` runs a
fake firmware in-process. See [docs/HARDWARE.md](docs/HARDWARE.md).

## Development

```bash
pytest          # 64 tests, ~35 s (live Claude test is skipped without credentials)
cyberdyne verify
ruff check .
```

## License

Eclipse Public License 2.0.
