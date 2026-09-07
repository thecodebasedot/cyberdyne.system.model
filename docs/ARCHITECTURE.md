# Cyberdyne System Model — Architecture

A layered cognitive robotics platform. The goal of the Foundation phase is not
to be feature-complete; it is to fix the **contracts** every later layer plugs
into, so that a custom RTOS, a VLA model, a fleet protocol or a formal
verifier can be added without rewriting anything beneath it.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 14 Developer platform     cli.py, skills SDK, scenarios/, tests/         │
│ 13 Observability          observability/  telemetry + web dashboard       │
│ 10 Fleet                  fleet/          (reserved: transport, sync)     │
│  9 Skills                 skills/         manifest + permission + runner  │
│  7 Language               language/       rule interpreter -> LLM later  │
│  6 Learning               learning/       (reserved: replay, learner)     │
│  4 Cognition              cognition/      behaviour tree, planner, brain  │
│  5 Memory                 memory/         working + episodic              │
│  3 World model            world_model/    occupancy grid + entities       │
│  8 Motion                 motion/         A* planner + local steering     │
│  2 Perception             perception/     sensor hub, range perception    │
│ 11 Safety                 safety/         envelope, e-stop, perms, audit  │
│  1 HAL                    hal/            interfaces + virtual backend    │
│ 12 Simulation             sim/            2D world, scenarios, chaos      │
│  0 Kernel                 kernel/         clock, bus, scheduler, state    │
└──────────────────────────────────────────────────────────────────────────┘
```

## Kernel (`cyberdyne/kernel`)

| Piece | Contract |
|---|---|
| `Clock` | The only source of time. `SimClock` is discrete-event (jumps to the next deadline) with an optional real-time factor; `WallClock` is monotonic. Swapping them changes nothing else. |
| `MessageBus` | Typed pub/sub. Topics are `layer/name`; subscriptions accept globs. A failing handler is isolated and reported on `kernel/fault`. Keeps latest-per-topic and a bounded history for replay and dashboards. |
| `Module` | Unit of scheduling and restart: `setup / tick / teardown`, `rate_hz`, `priority`, `critical`. |
| `Scheduler` | Single frame loop, priority ordered, deterministic. Tracks per-module ticks, faults, deadline misses. `max_faults` consecutive failures park a module in FAULT; a critical FAULT engages the e-stop. |
| `Watchdog` | Restarts stale/faulted modules after a cooldown with a restart budget; audits every restart. |
| `StateMachine` | Whitelisted transitions `BOOT → DIAGNOSTIC → IDLE ⇄ ACTIVE ⇄ CHARGING`, `* → ESTOP → DIAGNOSTIC`, `* → SHUTDOWN`. Illegal transitions raise. |
| `RobotConfig` | Plain dataclasses; loadable from TOML/JSON. Scenarios are configs plus an event timeline. |

Future work that lands here: process isolation (one process per module over
ZeroMQ), Protobuf message schemas, deterministic replay from bus history,
the custom RTOS (Layer A) behind the same `Clock`/`Scheduler` contracts.

## HAL (`cyberdyne/hal`)

Abstract devices: `DriveBase`, `RangeSensor`, `Battery`, `IMU` (camera, mic,
speaker kinds are declared, drivers pending). A `DeviceRegistry` is the only
way modules obtain hardware. The `virtual/` backend binds every interface to
the in-process `sim.World`; a Raspberry Pi / ROS 2 / Gazebo backend
implements the same classes. `self_test()` on every device feeds the boot
diagnostic; a failed self-test boots straight into ESTOP.

## Simulation (`cyberdyne/sim`)

2D world with axis-aligned obstacles, differential-drive kinematics, sliding
contact, ray-cast range sensing and a charging pad. `ScenarioEvents` replays a
scripted timeline (`[[events]] at/topic/payload`) which is how e-stop drills,
sensor fault injection (`sim/inject_fault`) and user utterances are tested.

## Safety core (`cyberdyne/safety`)

The one layer nothing above may reconfigure at runtime (`safety.*` is
FORBIDDEN in the permission policy).

* `SafetyGate` is the **only** writer to the drive base. Everything else
  publishes `motion/cmd` and the gate decides what reaches the motors.
* `SafetyEnvelope` clamps speed, stops for obstacles inside
  `obstacle_stop_distance`, refuses to enter keep-out zones.
* Perception staleness: no fresh scan within `perception_stale` seconds means
  no forward motion. A blind robot does not drive.
* `EStop` is a latch. Engaging is free; reset is a CONFIRM-tier action.
* `PermissionPolicy` tiers every action FREE / LOG / CONFIRM / FORBIDDEN;
  most specific glob wins; `self.modify` is FORBIDDEN by default.
* `AuditLog` is SHA-256 hash-chained; `verify()` finds the first tampered
  entry. Boot, shutdown, e-stops, violations, module faults/restarts, brain
  mode changes and logged skill invocations all land here.

## Perception → World model → Memory

* `SensorHub` publishes `sensor/odometry|battery|imu`.
* `RangePerception` publishes `perception/scan`, `perception/front_clearance`,
  `perception/obstacles`.
* `WorldModel` fuses scans into a log-odds `OccupancyGrid` and keeps an
  `EntityStore` (charger, waypoints, self; people/objects later).
* `MemoryModule` turns notable bus events into `EpisodicMemory` episodes with
  importance + recency retrieval and capacity-driven consolidation
  (forgetting); `WorkingMemory` is a TTL scratchpad.
* `EpisodicMemory.search(text)` is vector-backed through `TextIndex`, whose
  `Embedder` is pluggable (`HashEmbedder` is dependency-free hashed
  bag-of-words + trigrams; a sentence-transformer drops in unchanged).
* `KnowledgeGraph` is semantic memory: (subject, predicate, object) facts,
  newest wins, neighbour queries. `remember` writes facts, `recall <query>`
  searches both stores, `forget <x>` erases a subject from every store and
  audits it (the privacy primitive).

## Cognition (`cyberdyne/cognition`)

`Brain` runs a behaviour tree at 2 Hz over a blackboard rebuilt from the bus:

```
Selector
├─ Sequence[estop engaged]      → hold
├─ Sequence[battery low]        → go to charger, stay until full
├─ Sequence[has plan]           → execute plan steps (goto / skill)
├─ Sequence[has patrol]         → next waypoint, count laps
└─ idle                         → IDLE or CHARGING if on the pad
```

Free-form goals arrive on `brain/goal` and go through the **council**
(below). The brain owns every navigation goal so safety pre-emption
(battery, e-stop) always wins over user requests.

### The council (Phase 2)

```
brain/goal ──> Perceiver ──> brief ──┐
                                     ├─> Planner (rule | LLM) ──> Plan
                                     │        │
                                     │        ├─> SafetyOfficer: Constitution.review  ─> violations
                                     │        └─> Critic: MentalSimulator.rollout      ─> confidence, critique
                                     └─────────────────────────────────────────────────┘
                                                             │
                                          Decision{approved | question + options}
                                                             │
                              approved ──> brain adopts plan ──> nav/goal / skill/invoke
                              held     ──> brain/question ──> human/answer ──> proceed | cancel | new goal
```

* **Perceiver** compresses the bus into a situation brief (pose, battery,
  e-stop, world bounds, keep-out zones, known entities, explored fraction,
  available skills with their argument names). The same brief is what the
  LLM planner sees, so what the model knows is exactly what is logged.
* **Planner** is `RulePlanner` (goto / patrol / charge / stop) or
  `LLMPlanner`, which asks the model for a JSON plan and falls back to the
  rules on any transport, refusal or parse failure. Fallbacks are marked in
  the plan rationale and lower the critic's confidence.
* **SafetyOfficer** applies the `Constitution`: unknown skills, forbidden or
  confirm-tier actions, goals out of bounds, inside obstacles or keep-out
  zones, plans that are too long. Hard violations (`keep_out`,
  `out_of_bounds`, `inside_obstacle`, `forbidden_action`, `unknown_skill`)
  can never be overridden by a human answer; `needs_confirmation` can.
* **Critic** runs the `MentalSimulator`: an imagined `World` built only from
  the occupancy grid (belief, not ground truth), each goto rolled out with a
  kinematic follower on the A* path. It reports reachability, time,
  distance and predicted battery; confidence falls for infeasible steps,
  battery below reserve, an unexplored map, long plans or a rule fallback.
* **Decision** is audited and published on `brain/decision`. Below the
  confidence threshold, or with any violation, the brain publishes
  `brain/question` (with options) instead of acting. `human/answer` closes
  it: `proceed` re-deliberates with confirmation, `cancel` drops it,
  anything else is a new goal. Unanswered questions time out.
* **Explain**: the `explain` skill returns the last decision (goal, plan,
  rationale, violations, critique, prediction) so "keno korle?" has an
  answer grounded in what actually happened.

### LLM seam (`cognition/llm.py`)

`LLMBackend.complete(system, prompt, effort)` is the only model call in the
codebase. `AnthropicBackend` uses the official async SDK (`claude-opus-5`
by default, adaptive thinking, `output_config.effort`, cached system prompt,
typed error handling, `refusal` stop reason surfaced as `LLMRefused`).
`ScriptedBackend` returns canned text for tests. `build_backend(kind)` maps
the `[brain] llm` config value to a backend; `none` runs the robot fully
rule-based. The `LanguageModule` uses `LLMInterpreter`, which tries the
rule interpreter first (free, deterministic) and only asks the model for
utterances the rules do not understand.

## Motion (`cyberdyne/motion`)

Two-level navigation. `GridPlanner` runs A* over the occupancy grid (unknown
= free, occupied cells inflated, no corner cutting) and replans every 2 s.
The local controller scores every scan beam as a candidate heading by goal
deviation, free space along the beam and its neighbours, and continuity with
the previously committed heading (hysteresis against dithering). Stuck
detection backs off, forces a replan and publishes `nav/recovery`.

## Skills and language

A `Skill` is a `SkillManifest` (name, version, args, permission action) plus
an async `run`. `SkillRunner` checks the permission policy before code runs,
audits LOG/CONFIRM invocations, enforces a timeout and publishes
`skill/result`. Built-ins: `time`, `echo`, `status`, `goto`, `estop`,
`estop_reset`, `remember`, `recall`. Extra skill modules are listed in
config (`skills = ["my.pkg.skills"]`).

`LanguageModule` turns `language/utterance` into `skill/invoke` through an
`Interpreter`. `RuleInterpreter` understands English and romanised Bangla
(`jao 3 4`, `charge koro`, `thamo`, `koyta baje`, `mone rakho x = y`); it is
the fallback and the test oracle for the LLM interpreter.

## Observability

`Telemetry` builds a JSON snapshot of the entire robot (state, safety, audit
tail, pose, scan, brain, path, grid, entities, memory, scheduler stats, recent
events). `Dashboard` serves it over HTTP from a thread with a canvas control
room: map, scan rays, planned path, keep-out zones, occupancy overlay,
module health, events, audit chain. Commands go back through the bus
(`/api/say`, `/api/estop`, `/api/goal`).

## Bus topic reference

| Topic | Producer | Payload |
|---|---|---|
| `kernel/state` | StateMachine | `{from, to, reason}` |
| `kernel/fault` | Bus/Scheduler | `{kind, module|handler, error}` |
| `kernel/module_fault` / `module_restart` / `module_stale` | Scheduler/Watchdog | `{module, ...}` |
| `kernel/diagnostic` | Runtime | `{device_id: {ok, msg}}` |
| `sensor/odometry` | SensorHub | `{x, y, theta, linear, angular}` |
| `sensor/battery` | SensorHub | `{level, charging, voltage}` |
| `perception/scan` | RangePerception | `[{angle, distance}]` |
| `perception/front_clearance` | RangePerception | metres |
| `world/summary` | WorldModel | `{explored, entities, grid_updates}` |
| `brain/goal` | skills / users | `{goal: "...", confirmed?}` |
| `brain/decision` | Brain (council) | `Decision.to_dict()` |
| `brain/question`, `brain/question_closed` | Brain | `{id, question, options, goal}` / `{id, outcome}` |
| `human/answer` | dashboard / `answer` skill | `{answer, id?}` |
| `brain/plan`, `brain/plan_done`, `brain/state`, `brain/lap` | Brain | |
| `nav/goal`, `nav/cancel` | Brain | `{x, y, name}` |
| `nav/path`, `nav/status`, `nav/arrived`, `nav/recovery` | MotionController | |
| `motion/cmd` | MotionController | `{linear, angular}` requested |
| `motion/cmd_applied` | SafetyGate | `{linear, angular}` after clamping |
| `safety/estop` | anyone | `{engage, reason, confirmed}` |
| `safety/estop_state`, `safety/violation` | SafetyGate | |
| `skill/invoke`, `skill/result` | Language/Brain → SkillRunner | |
| `language/utterance`, `language/intent`, `language/unknown` | | |
| `sim/collision`, `sim/inject_fault` | SimStepper | chaos hooks |
| `telemetry/heartbeat` | Telemetry | |

## Where the rest of the vision lands

| Vision layer | Home in this tree | Hook that already exists |
|---|---|---|
| Custom RTOS, formal verification (A) | `kernel/` | `Clock`/`Scheduler` contracts, deterministic frames |
| Custom silicon, FPGA perception (B) | `hal/` drivers | `Device` interface, registry, self-test |
| Global-workspace cognition, homeostasis, dreaming (C) | `cognition/`, `memory/` | blackboard + BT, episodic consolidation |
| Custom ML stack, VLA, neural world model (D) | `perception/`, `world_model/`, `cognition/planner.py` | `Planner`, `Interpreter`, `LLMBackend`, `Embedder` ABCs |
| Global workspace / metacognition (C) | `cognition/council.py` | brief -> plan -> critique -> question loop |
| Constitutional rules over LLM output (F) | `cognition/constitution.py` | deterministic review, hard vs overridable |
| Mental simulation (C) | `cognition/simulate.py` | belief-only imagined world |
| Self-modification (E) | `learning/`, `skills/` | `Learner.propose`, `self.modify` FORBIDDEN gate |
| Runtime verification, shielded RL (F) | `safety/` | `SafetyGate` as sole actuator writer |
| Robot society / fleet economy (G) | `fleet/` | `FleetMessage`, `Transport` |
| Deep human modelling (H) | `world_model/entities.py`, `memory/` | `EntityStore` |
| Security hardening, immune system (I, J) | `safety/audit.py`, `kernel/watchdog.py` | hash chain, restart budget |
| Simulation at scale, digital twin (K, L) | `sim/`, `scenarios/` | scenario DSL, fault injection |
| Compliance and ethics (M) | `safety/permissions.py`, audit | tiers + tamper-evident log |
| Ops, OTA, teleop (N) | `observability/` | telemetry snapshot, dashboard API |
| Meta-platform (O) | everything above | |
