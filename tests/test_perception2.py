"""Batch 2: pose fusion, motion prediction, scene graph, anomalies, affect, sound bearing, PyBullet backend."""
import math

import pytest

from cyberdyne.hal.interfaces import Detection
from cyberdyne.language.affect import mood
from cyberdyne.perception.fusion import PoseFilter
from cyberdyne.perception.vision import EntityTracker
from cyberdyne.runtime import Runtime
from cyberdyne.sim.scenario import load_scenario
from cyberdyne.world_model.anomaly import AnomalyDetector
from cyberdyne.world_model.scene import describe, relations
from tests.conftest import booted, run


def test_pose_filter_corrects_drift_with_imu_and_walls():
    f = PoseFilter(1.0, 1.0, 0.0, 10.0, 10.0)
    f.predict(1.0, 1.0, 0.0)
    f.predict(2.0, 1.0, 0.1)                       # odometry says we turned 0.1 rad; we did not
    for _ in range(20):
        f.update_heading(0.0)                      # IMU insists on 0
    assert abs(f.theta) < 0.02
    # robot really at x=2: the wall ahead (x=10) should read 8.0, odometry-drifted estimate is off
    f.x = 2.5
    for _ in range(30):
        f.update_walls([{"angle": 0.0, "distance": 8.0}, {"angle": math.pi / 2, "distance": 9.0}], 20.0)
    assert abs(f.x - 2.0) < 0.15 and f.wall_fixes == 30
    f.fix(0.5, 0.5)
    assert (f.x, f.y) == (0.5, 0.5)


def test_fusion_beats_raw_odometry_on_patrol():
    cfg = load_scenario("patrol")
    cfg.world.odometry_noise = 0.05
    cfg.world.imu_noise = 0.02

    async def go():
        rt = await booted(cfg)
        await rt.run(120)
        true = rt.world.robot
        fused = rt.bus.latest_payload("sensor/odometry")
        raw = rt.bus.latest_payload("sensor/odometry_raw")
        await rt.shutdown()
        return (math.hypot(fused["x"] - true.x, fused["y"] - true.y),
                math.hypot(raw["x"] - true.x, raw["y"] - true.y), rt.report())
    e_fused, e_raw, rep = run(go())
    assert e_fused < 0.4 and e_fused < e_raw / 2
    assert rep["world"]["collisions"] == 0 and rep["brain"]["laps"] >= 1


def test_tracker_predicts_motion():
    tr = EntityTracker()
    pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
    for i in range(6):
        tr.update([Detection("person", "person", 0.0, 2.0 + 0.5 * i, 0.9)], pose, i * 0.5)
    t = tr.tracks[1]
    vx, vy = t.velocity()
    assert vx == pytest.approx(1.0, abs=0.3) and abs(vy) < 0.1


def test_scene_relations_and_description():
    ents = [{"id": "object:keys", "kind": "object", "x": 5.0, "y": 5.0, "attrs": {"room": "living"}, "last_seen": 10.0},
            {"id": "person:Ijtihad", "kind": "person", "x": 5.5, "y": 5.0, "attrs": {"room": "living"},
             "last_seen": 10.0},
            {"id": "old", "kind": "object", "x": 1.0, "y": 1.0, "attrs": {}, "last_seen": 0.0},
            {"id": "self", "kind": "robot", "x": 5.0, "y": 3.0, "attrs": {}, "last_seen": 10.0}]
    rel = relations(ents, {"x": 5.0, "y": 3.0, "theta": math.pi / 2}, now=10.0)
    assert ("object:keys", "in", "living") in rel and ("object:keys", "near", "person:Ijtihad") in rel
    assert ("object:keys", "ahead_of", "robot") in rel and not any(a == "old" for a, _, _ in rel)
    text = describe(rel, "living")
    assert text.startswith("I am in the living.") and "keys is near Ijtihad" in text


def test_anomaly_detector():
    d = AnomalyDetector(displacement=1.0, min_sightings=3, settle_time=10.0)
    obj = {"id": "object:keys", "kind": "object", "x": 5.0, "y": 5.0, "seen_count": 5}
    assert not any(d.observe(obj, 1.0, "morning") for _ in range(4))
    moved = {**obj, "x": 8.0}
    out = d.observe(moved, 50.0, "morning")
    assert out and out[0]["kind"] == "displaced"
    cup = {"id": "object:cup", "kind": "object", "x": 1.0, "y": 1.0, "seen_count": 1}
    assert d.observe(cup, 99.0, "morning")[0]["kind"] == "new_object"
    person = {"id": "person:Rafi", "kind": "person", "x": 1, "y": 1, "seen_count": 9}
    d.observe(person, 1.0, "morning")
    d.observe(person, 2.0, "afternoon")
    assert d.observe(person, 3.0, "night")[0]["kind"] == "unusual_hour"


def test_affect_lexicon():
    assert mood("tumi khub bhalo")[0] > 0.9
    assert mood("this is useless and slow")[0] < 0
    assert mood("go to the kitchen") == (0.0, [])


def test_home_scene_affect_bearing_and_describe():
    cfg = load_scenario("home")
    cfg.events = []
    cfg.brain.patrol = []

    async def go():
        rt = await booted(cfg)
        log = []
        for t in ("language/affect", "speech/heard", "nav/face", "skill/result", "world/scene"):
            rt.bus.subscribe(t, lambda m, t=t: log.append((t, m.payload)))
        await rt.run(3)
        await rt.bus.publish("sim/hear", {"text": "cyberdyne tumi khub bhalo", "signature": "face:ijtihad"})
        await rt.run(3)
        await rt.bus.publish("sim/hear", {"text": "cyberdyne ki dekhcho", "signature": "face:ijtihad"})
        await rt.run(2)
        await rt.shutdown()
        return rt, log
    rt, log = run(go())
    affect = [p for t, p in log if t == "language/affect"]
    assert affect and affect[0]["speaker"] == "Ijtihad" and affect[0]["mood"] > 0.5
    assert rt.user_model.model.mood["Ijtihad"] > 0
    heard = [p for t, p in log if t == "speech/heard"]
    assert heard and abs(heard[0]["bearing"]) > 0.3
    assert any(t == "nav/face" for t, _ in log)                    # idle robot turned to the speaker
    desc = [p for t, p in log if t == "skill/result" and p["skill"] == "describe"]
    assert desc and "I am in the hall" in desc[0]["output"]["speech"]
    assert any(t == "world/scene" and p["relations"] for t, p in log)


@pytest.mark.skipif(pytest.importorskip("pybullet", reason="pybullet not installed") is None, reason="no pybullet")
def test_bullet_backend_drives_and_senses():
    cfg = load_scenario("home")
    cfg.kernel.mode = "bullet"
    cfg.events = []

    async def go():
        rt = Runtime(cfg)
        await rt.boot()
        assert {d.kind.value for d in rt.devices.all()} >= {"drive", "range", "battery", "camera"}
        start = rt.bus.latest_payload("sensor/odometry") or {"x": 1.0, "y": 1.0}
        await rt.run(25)
        pose = rt.bus.latest_payload("sensor/odometry")
        scan = rt.bus.latest_payload("perception/scan")
        rep = rt.report()
        await rt.shutdown()
        return start, pose, scan, rep
    start, pose, scan, rep = run(go())
    assert math.hypot(pose["x"] - 1.0, pose["y"] - 1.0) > 2.0            # it moved under physics
    assert any(p["distance"] < 4.0 for p in scan)                          # rays hit walls
    assert rep["scheduler"]["faults"] == 0 and rep["state"] == "shutdown"
