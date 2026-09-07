"""Phase 5: schemas, isolation, recording/replay determinism, fleet, auction, OTA, verification."""
import json

import pytest

from cyberdyne.fleet.sim import FleetSim
from cyberdyne.kernel.config import SafetyConfig
from cyberdyne.kernel.isolation import IsolatedModule, PureModule
from cyberdyne.kernel.schema import SchemaError, validate
from cyberdyne.kernel.state import _TRANSITIONS, SystemState
from cyberdyne.observability.recording import Recording
from cyberdyne.ops.release import Bundle
from cyberdyne.runtime import Runtime
from cyberdyne.safety.verify import check_gate, check_state_machine, verify_all
from cyberdyne.sim.scenario import load_scenario
from tests.conftest import booted, run


def test_schema_validation():
    assert validate("sensor/odometry", {"x": 1, "y": 2.0, "theta": 0.1, "linear": 0.0, "angular": 0.0})
    assert validate("nav/goal", {"x": 1.0, "y": 2.0})
    assert validate("nav/cancel", None)
    assert validate("unknown/topic", {"anything": 1}) is False
    for topic, payload in (("sensor/odometry", {"x": "1"}), ("nav/goal", {"x": 1.0}), ("nav/cancel", {}),
                           ("perception/scan", [{"angle": 0.0}]), ("sensor/battery", {"level": True, "charging": 1,
                                                                                       "voltage": 1.0})):
        with pytest.raises(SchemaError):
            validate(topic, payload)


def test_every_scenario_passes_strict_bus():
    for name in ("patrol", "voice", "council", "home", "chores"):
        cfg = load_scenario(name)
        cfg.kernel.strict_bus = True

        async def go(cfg=cfg):
            rt = await booted(cfg)
            await rt.run(30)
            await rt.shutdown()
            return rt
        rt = run(go())
        assert rt.bus.schema_errors == 0 and rt.report()["scheduler"]["faults"] == 0, name


class Doubler(PureModule):
    inputs = ("iso/in",)

    def compute(self, dt, inputs):
        b = inputs.get("iso/in") or {"level": 1.0}
        if b["level"] < 0:
            raise ValueError("negative battery")
        return [("iso/double", {"value": b["level"] * 2})]


class Hanger(PureModule):
    def compute(self, dt, inputs):
        import time
        time.sleep(10)
        return []


def test_isolated_module_runs_crashes_and_restarts(config):
    config.brain.patrol = []

    async def go():
        rt = Runtime(config)
        iso = IsolatedModule(Doubler, "doubler", rate_hz=5.0)
        rt.scheduler.register(iso)
        rt.watchdog.cooldown = 0.5
        await rt.boot()
        await rt.run(2)
        assert rt.bus.latest_payload("iso/double")["value"] == pytest.approx(2.0, abs=0.05)
        assert iso.spawns == 1
        await rt.bus.publish("iso/in", {"level": -1.0})
        await rt.run(0.3)
        assert iso.state.value == "fault"
        await rt.bus.publish("iso/in", {"level": 0.5})          # poison gone; the restart can succeed
        await rt.run(2)
        assert iso.state.value == "running" and iso.spawns == 2 and iso.stats.restarts == 1
        await rt.shutdown()
    run(go())


def test_isolated_module_hang_is_a_fault(config):
    config.brain.patrol = []

    async def go():
        rt = Runtime(config)
        iso = IsolatedModule(Hanger, "hanger", rate_hz=5.0, timeout=0.3)
        rt.scheduler.register(iso)
        await rt.boot()
        await rt.run(0.5)
        assert iso.stats.faults >= 1 and iso.state.value == "fault"
        await rt.shutdown()
    run(go())


def test_recording_is_deterministic_and_queryable(tmp_path):
    digests = []
    for i in range(2):
        cfg = load_scenario("voice")
        cfg.kernel.record = str(tmp_path / f"run{i}.jsonl")

        async def go(cfg=cfg):
            rt = await booted(cfg)
            await rt.run(20)
            await rt.shutdown()
        run(go())
        digests.append(Recording.load(cfg.kernel.record).digest())
    assert digests[0] == digests[1]
    rec = Recording.load(tmp_path / "run0.jsonl")
    assert rec.duration >= 19.9 and "language/intent" in rec.topics()
    early, late = rec.state_at(0.5), rec.state_at(15.0)
    assert "language/intent" not in early and late["language/intent"]["skill"] == "goto"
    goals = rec.between(0.0, 5.0, "nav/goal")
    assert goals and goals[0]["payload"]["x"] == 8.0
    assert rec.summary()["messages"] == len(rec.messages)


def _fleet_configs(n: int):
    cfgs = []
    for i in range(n):
        c = load_scenario("home")
        c.name = f"r{i + 1}"
        c.events = []
        c.brain.patrol = []
        c.world.robot_start = [{"x": 1.0, "y": 1.0, "theta": 0.0}, {"x": 4.0, "y": 1.0, "theta": 0.0},
                               {"x": 8.5, "y": 4.5, "theta": 3.1416}][i]
        c.social.armed = False
        cfgs.append(c)
    return cfgs


def test_fleet_presence_entity_sync_and_auction():
    async def go():
        fs = FleetSim(_fleet_configs(3))
        awards = []
        fs.robots[0].bus.subscribe("fleet/awarded", lambda m: awards.append(m.payload))
        await fs.boot()
        await fs.run(6)
        for rt in fs.robots:
            assert set(rt.ctx.extras["fleet"].peers) == {r.config.name for r in fs.robots} - {rt.config.name}
        # only r3 (at (8.5, 4.5) facing -x) can see the keys at (6.6, 4.5); the table hides them from the others
        seen = {rt.config.name: rt.world_model.find("keys") for rt in fs.robots}
        assert any(seen.values())
        assert all(seen.values())                                          # LWW sync spread the sighting
        origin = next(rt for rt in fs.robots if seen[rt.config.name]["attrs"].get("fleet") is None)
        await fs.robots[0].bus.publish("fleet/auction", {"name": "corner", "steps": [
            {"kind": "goto", "args": {"x": 9.0, "y": 1.0, "name": "corner"}}]}, source="test")
        await fs.run(4)
        assert awards and awards[0]["awarded"] == "r3"                      # closest robot wins
        assert len(awards[0]["bids"]) == 3
        await fs.run(20)
        winner = fs.get("r3")
        assert winner.tasks.completed == 1
        assert all(rt.tasks.completed == 0 for rt in fs.robots if rt is not winner)
        await fs.shutdown()
        return fs, origin
    fs, origin = run(go())
    rep = fs.report()
    assert rep["r3"]["auction"]["won"] == 1 and rep["r1"]["auction"]["lost"] == 1
    assert origin.config.name == "r3"


def test_ota_bundle_apply_commit_rollback(config):
    config.brain.patrol = []
    config.safety.permissions = {"self.modify": "confirm"}
    bundle = {"version": "1.1.0", "routines": [{"name": "ping", "every": 1.0, "speak": "ping"}],
              "tasks": {"blink": [{"kind": "wait", "args": {"seconds": 0.2}}]},
              "macros": [{"name": "blinker", "description": "", "steps": [{"kind": "task", "args": {"name": "blink"}}],
                          "params": {}}]}

    async def go():
        rt = await booted(config)
        ops = rt.ctx.extras["ops"]
        ops.health_window = 3.0
        log = []
        for t in ("ops/activated", "ops/committed", "ops/rolled_back", "ops/rejected", "speech/said"):
            rt.bus.subscribe(t, lambda m, t=t: log.append((t, m.payload)))
        await rt.bus.publish("ops/update", {**bundle, "safety": {"max_linear": 9}})
        await rt.run(0.5)
        assert log[-1][0] == "ops/rejected"
        await rt.bus.publish("ops/update", {**bundle, "permissions": {"safety.reconfigure": "free"}})
        await rt.run(0.5)
        assert log[-1][0] == "ops/rejected" and "safety." in log[-1][1]["problems"][0]
        await rt.bus.publish("ops/update", bundle)
        await rt.run(4)
        kinds = [t for t, _ in log]
        assert "ops/activated" in kinds and "ops/committed" in kinds
        assert "blinker" in rt.ctx.extras["skills"].names() and "blink" in rt.tasks.library
        assert any(t == "speech/said" and p["text"] == "ping" for t, p in log)
        # a second bundle that breaks a task rolls back automatically during its health window
        bad = {"version": "1.2.0", "tasks": {"boom": [{"kind": "skill", "args": {"skill": "nope"}, "retries": 0}]}}
        await rt.bus.publish("ops/update", bad)
        await rt.run(0.5)
        assert ops.active.version == "1.2.0" and "blinker" not in rt.ctx.extras["skills"].names()
        await rt.bus.publish("task/start", {"name": "boom"})
        await rt.run(1.5)
        assert ops.active.version == "1.1.0" and "blinker" in rt.ctx.extras["skills"].names()
        assert log[-1][0] == "ops/rolled_back" and "health" in log[-1][1]["reason"]
        assert await ops.rollback("manual") and ops.active is None
        assert "blinker" not in rt.ctx.extras["skills"].names()
        await rt.shutdown()
        return rt
    rt = run(go())
    assert [e.action for e in rt.safety.audit.entries if e.actor == "ops"][:3] == \
        ["update.rejected", "update.rejected", "update.activated"]
    assert Bundle.from_dict(bundle).hash == Bundle.from_dict(json.loads(json.dumps(bundle))).hash


def test_safety_verification_passes_and_catches_bad_tables():
    res = verify_all()
    assert res["ok"] and res["gate"]["states_checked"] > 1000 and res["state_machine"]["violations"] == []
    assert check_gate(SafetyConfig(max_linear=0.3, obstacle_stop_distance=0.5, battery_critical=0.2)).ok
    bad = {k: set(v) for k, v in _TRANSITIONS.items()}
    bad[SystemState.ESTOP].add(SystemState.ACTIVE)
    bad[SystemState.BOOT].add(SystemState.IDLE)
    bad[SystemState.CHARGING].discard(SystemState.ESTOP)
    v = check_state_machine(bad)
    assert not v.ok and {x[:2] for x in v.violations} == {"I6", "I7", "I8"}
