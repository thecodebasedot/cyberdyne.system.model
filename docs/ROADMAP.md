# Roadmap

## Phase 1 — Foundation (this commit)
- [x] Kernel: sim/wall clock, message bus, frame scheduler, module lifecycle, watchdog, state machine, config
- [x] HAL interfaces + virtual backend bound to a 2D physics world
- [x] Safety core: envelope, keep-out zones, perception staleness guard, e-stop latch, permission tiers, hash-chained audit
- [x] Perception (range), occupancy-grid world model, entity store
- [x] Working + episodic memory with consolidation
- [x] Behaviour-tree brain, rule planner, patrol/charge/plan policies
- [x] A* global planner + gap-based local controller with hysteresis and recovery
- [x] Skill system with manifests, permissions, timeouts; 8 built-ins
- [x] Rule interpreter (English + romanised Bangla)
- [x] Telemetry + web dashboard with command API
- [x] Scenario DSL with scripted events and fault injection; 4 scenarios
- [x] CLI: `run`, `check`, `say`, `scenario list`, `skills`
- [x] 24 tests covering every layer plus end-to-end runs

## Phase 2 — Mind (done)
- [x] `LLMBackend` seam; `AnthropicBackend` (official SDK, optional extra) and `ScriptedBackend` for tests
- [x] `LLMPlanner` + `LLMInterpreter` behind the existing ABCs, rule versions as fallback and fast path
- [x] Council: Perceiver / Planner / SafetyOfficer / Critic -> audited `Decision`
- [x] Constitution: deterministic hard-rule review of every plan (bounds, keep-out, obstacles, permissions, unknown skills)
- [x] Mental simulation on the believed map before acting; feeds the critic's confidence
- [x] Metacognition: confidence threshold, `brain/question` <-> `human/answer`, timeouts, `explain` skill
- [x] Semantic memory (knowledge graph), vector-backed episodic search, `forget` privacy primitive
- [x] Dashboard: council panel, question/answer box, `/api/plan`, `/api/answer`; `council` scenario; 10 tests
- Deferred to Phase 4: multi-turn dialogue memory for the LLM interpreter; learned confidence calibration

## Phase 3 — Body (done in simulation; serial backend ready for a board)
- [x] Sim actors: people on routes and objects, seen by the range sensor and the camera, occluded by walls, yield to the robot
- [x] `Camera` / `Microphone` / `Speaker` HAL interfaces + virtual drivers; `Detection`/`Frame`/`Utterance` types
- [x] `VisionPerception` with `EntityTracker` (persistent ids, gating, smoothing, expiry); `perception/tracks`, `perception/people`
- [x] `SocialModule` + `IdentityRegistry`: recognise known people, greet (rate-limited), security mode alerts on strangers
- [x] `VoiceModule`: wake-word gating, speaker identity and trust on every utterance, robot speaks questions/results
- [x] Trust-gated permissions: owner / guest / unknown ceilings on skill tiers
- [x] Rooms in the world model, room labels on every entity, `find` skill ("amar keys kothay"), place-name goals ("kitchen e jao")
- [x] Social navigation: personal-space cost layer in A*, slow-down near people
- [x] Serial bridge backend (line protocol, `LoopbackTransport` fake firmware, pyserial optional), `mode = "serial"`, drive calibration; docs/HARDWARE.md
- Follow-ups: real camera (OpenCV + detector), real mic (Whisper/Vosk), TTS, IMU over serial, PyBullet backend, Raspberry Pi GPIO drivers

## Phase 4 — Skills and learning (done)
- [x] `TaskRunner`: goto / skill / wait / sub-task steps, retries, timeouts, pause/resume, cancel, queue; brain executes plans through it and pauses tasks for battery or e-stop
- [x] `RoutineModule`: `every` (kernel clock), `at HH:MM` (wall clock), one-shot reminders; `remind` skill
- [x] Home automation: `DeviceHub` with `VirtualHub`, `MQTTHub` (paho, optional), `HassHub` (REST); `device` skill resolves room from the world model; Bangla phrases ("kitchen er light jalao")
- [x] Learning from demonstration: `DemoRecorder` + `teach` skill turn human-sent goals and device commands into a replayable task
- [x] `UserModel`: per-person habits by time of day; proactive *suggestions* (never actions) when a habit is seen enough
- [x] `ControllerTuner`: (1+1)-ES over local-controller parameters in simulation, scored on laps / distance / contacts / recoveries / collisions; `cyberdyne train`; the safety gate is outside the search space
- [x] Self-authored **macro** skills (declarative steps + templates, no code), validated by the constitution, gated by `self.modify` (FORBIDDEN by default, CONFIRM when the owner enables it)
- [x] `Store`: JSON persistence of facts, episodes, user model, taught tasks and macros across runs (`[learning] data_dir`)
- Follow-ups: mood/affect estimation, RL policy for local control (the tuner is the shielded harness for it), LLM-drafted macros through the same validator

## Phase 5 — Scale
- [ ] Fleet transport (ZeroMQ / MQTT), shared world model sync (CRDT), task auction
- [ ] Process isolation per module, Protobuf schemas
- [ ] Deterministic replay and time-travel debugging from bus history
- [ ] OTA update, rollback, fleet console
- [ ] Formal verification of the safety gate (TLA+ model of the state machine + envelope)
