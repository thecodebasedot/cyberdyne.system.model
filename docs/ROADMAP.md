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

## Phase 3 — Body
- [ ] Camera device + object/face detection module; entity tracking with IDs
- [ ] Microphone / speaker devices; wake word; TTS
- [ ] Semantic SLAM (rooms, labels) on top of the occupancy grid
- [ ] PyBullet/MuJoCo backend behind the same HAL
- [ ] Raspberry Pi backend (GPIO motors, ultrasonic, IMU) + calibration pipeline
- [ ] Social navigation costs (personal space) in the planner

## Phase 4 — Skills and learning
- [ ] Skill composition and long-horizon tasks with interrupt/resume
- [ ] Home automation skills (MQTT / Home Assistant)
- [ ] Imitation + RL for local control in sim, shielded by the safety gate
- [ ] User model: preferences, routines, mood
- [ ] Sandboxed self-authored skills gated by `self.modify` permission

## Phase 5 — Scale
- [ ] Fleet transport (ZeroMQ / MQTT), shared world model sync (CRDT), task auction
- [ ] Process isolation per module, Protobuf schemas
- [ ] Deterministic replay and time-travel debugging from bus history
- [ ] OTA update, rollback, fleet console
- [ ] Formal verification of the safety gate (TLA+ model of the state machine + envelope)
