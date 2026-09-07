"""Phase 3: actors, vision tracking, identity/trust, voice, rooms, social nav, serial backend."""
import math

from cyberdyne.hal.calibration import calibrate_drive
from cyberdyne.hal.interfaces import Detection, DeviceKind
from cyberdyne.hal.registry import DeviceRegistry
from cyberdyne.hal.serial import LoopbackTransport, build_serial_devices
from cyberdyne.kernel.clock import SimClock
from cyberdyne.kernel.config import HardwareConfig, RobotConfig
from cyberdyne.motion.planner import GridPlanner
from cyberdyne.perception.vision import EntityTracker
from cyberdyne.runtime import Runtime
from cyberdyne.safety.permissions import PermissionDenied, PermissionPolicy
from cyberdyne.sim.scenario import load_scenario
from cyberdyne.sim.world import Actor, Obstacle, Pose, World
from cyberdyne.social.identity import IdentityRegistry, Trust
from cyberdyne.world_model.grid import OccupancyGrid
from tests.conftest import booted, run


def test_actors_move_yield_and_are_visible():
    w = World(width=10, height=10, obstacles=[Obstacle(4, 0, 1, 3)], robot=Pose(1, 1, 0),
              actors=[Actor("p", "person", 3.0, 1.0, "sig", route=[(3.0, 1.0), (1.4, 1.0)], speed=1.0),
                      Actor("hidden", "person", 6.0, 1.0, "sig2"), Actor("later", "person", 2.0, 2.0, active_from=50)])
    vis, bearing, dist = w.visible(w.actors[0], 1.2, 6.0)
    assert vis and abs(bearing) < 0.01 and math.isclose(dist, 2.0)
    assert not w.visible(w.actors[1], 1.2, 6.0)[0]                 # behind the wall
    assert len(w.active_actors()) == 2
    assert w.raycast(1, 1, 0, 6) < 3.0                              # range sensor sees the person
    for _ in range(300):
        w.step(0.01)
    assert w.actors[0].x > 1.4 and w.robot.distance_to(w.actors[0].x, w.actors[0].y) >= 0.5   # stopped short


def test_tracker_keeps_ids_and_expires():
    tr = EntityTracker(ttl=1.0)
    pose = {"x": 0.0, "y": 0.0, "theta": 0.0}
    a = tr.update([Detection("person", "person", 0.0, 2.0, 0.9, "")], pose, 0.0)
    b = tr.update([Detection("person", "person", 0.05, 2.1, 0.9, "face:x")], pose, 0.2)
    assert a[0].track_id == b[0].track_id == 1 and b[0].signature == "face:x" and b[0].hits == 2
    c = tr.update([Detection("person", "person", 1.0, 2.0, 0.9, "face:y")], pose, 0.4)
    assert {t.track_id for t in c} == {1, 2}
    assert tr.update([], pose, 5.0) == []


def test_identity_trust_and_permission_ceiling():
    reg = IdentityRegistry([{"name": "Ijtihad", "signature": "f1", "trust": "owner"},
                            {"name": "Rafi", "signature": "f2"}])
    assert reg.trust_of("f1") == Trust.OWNER and reg.trust_of("f2") == Trust.GUEST
    assert reg.trust_of("zzz") == Trust.UNKNOWN and Trust.OWNER.rank > Trust.GUEST.rank
    pol = PermissionPolicy()
    assert pol.check("skill.time", trust="unknown").value == "free"
    for trust, action in (("unknown", "skill.goto"), ("guest", "skill.arm"), ("guest", "skill.estop_reset")):
        try:
            pol.check(action, confirmed=True, trust=trust)
        except PermissionDenied:
            pass
        else:
            raise AssertionError(f"{trust} must not run {action}")
    assert pol.check("skill.goto", trust="guest").value == "log"
    assert pol.check("skill.arm", confirmed=True, trust="owner").value == "confirm"


def test_social_nav_cost_routes_around_people():
    g = OccupancyGrid(10, 10, 0.5)
    direct = GridPlanner().plan(g, (1.0, 5.0), (9.0, 5.0))
    social = GridPlanner().plan(g, (1.0, 5.0), (9.0, 5.0), soft=[(5.0, 5.0, 1.5, 6.0)])
    assert all(abs(y - 5.0) < 0.3 for _, y in direct)
    assert max(abs(y - 5.0) for _, y in social) > 1.0              # detoured around the person


def test_serial_loopback_backend_and_calibration():
    reg = DeviceRegistry()
    bridge = build_serial_devices(HardwareConfig(port="loopback"), reg, LoopbackTransport(wheel_scale=0.8))
    drive = reg.get(DeviceKind.DRIVE)
    clock = SimClock()

    async def go():
        assert (await drive.self_test())[0]
        scan = await reg.get(DeviceKind.RANGE).scan()
        assert len(scan) == 8 and scan[0].distance == 3.0
        assert 0 < (await reg.get(DeviceKind.BATTERY).read()).level <= 1.0
        res = await calibrate_drive(drive, clock, distance=1.0, speed=0.5, after_step=bridge.t.advance)
        assert math.isclose(res.linear_scale, 1.25, rel_tol=0.05)
        assert math.isclose(res.angular_scale, 1.25, rel_tol=0.05)
    run(go())
    assert bridge.errors == 0 and any(line.startswith("VEL") for line in bridge.t.received)


def test_runtime_in_serial_mode_with_loopback():
    cfg = RobotConfig()
    cfg.kernel.mode = "serial"
    cfg.brain.patrol = [{"x": 2.0, "y": 0.0}]

    async def go():
        rt = Runtime(cfg)
        await rt.boot()
        assert rt.world is None and rt.bridge is not None
        await rt.run(8)
        odom = rt.bus.latest_payload("sensor/odometry")
        assert odom["x"] > 0.5                                       # the fake firmware drove forward
        await rt.shutdown()
        return rt
    rt = run(go())
    assert rt.report()["scheduler"]["faults"] == 0


def test_home_scenario_social_behaviour():
    async def go():
        rt = await booted(load_scenario("home"))
        log = {t: [] for t in ("speech/said", "speech/overheard", "security/alert", "skill/result", "world/room",
                               "brain/question")}
        for t in log:
            rt.bus.subscribe(t, lambda m, t=t: log[t].append(m.payload))
        await rt.run(70)
        await rt.shutdown()
        return rt, log
    rt, log = run(go())
    said = [s["text"] for s in log["speech/said"]]
    assert "Hello Ijtihad." in said and said.count("Hello Ijtihad.") <= 2
    assert log["speech/overheard"][0]["text"] == "nice weather today"
    results = {(r["skill"], r["ok"]) for r in log["skill/result"]}
    assert ("find", True) in results and ("arm", False) in results and ("arm", True) in results
    assert [a["reason"] for a in log["security/alert"]] == ["unrecognised person while armed"]
    assert log["security/alert"][0]["x"] > 4.0                        # the stranger, in the kitchen
    rooms = [r["name"] for r in log["world/room"]]
    assert rooms[0] == "hall" and "kitchen" in rooms and "living" in rooms
    assert not log["brain/question"]                                  # kitchen goal was confident
    assert rt.report()["world"]["collisions"] < 50
    assert rt.world_model.find("keys")["attrs"]["room"] == "living"
    assert rt.world_model.find("ijtihad")["kind"] == "person"
    assert any(e.action == "security.alert" for e in rt.safety.audit.entries)
