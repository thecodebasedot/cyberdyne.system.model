"""End-to-end runs of the whole robot in simulation, as fast as the CPU allows."""
import json
import urllib.request

from cyberdyne.kernel.state import SystemState
from cyberdyne.runtime import Runtime
from cyberdyne.sim.scenario import load_scenario
from tests.conftest import booted, run


def test_boot_reaches_idle_with_clean_audit(config):
    async def go():
        rt = await booted(config)
        assert rt.state.state == SystemState.IDLE
        await rt.shutdown()
        return rt
    rt = run(go())
    assert rt.report()["audit"]["chain_ok"]
    assert rt.state.state == SystemState.SHUTDOWN


def test_patrol_completes_laps_without_collisions(config):
    config.world.battery_start = 1.0

    async def go():
        rt = await booted(config)
        await rt.run(150)
        await rt.shutdown()
        return rt
    rt = run(go())
    r = rt.report()
    assert r["brain"]["laps"] >= 1
    assert r["world"]["collisions"] == 0
    assert r["scheduler"]["faults"] == 0 and r["bus"]["errors"] == 0


def test_low_battery_goes_to_charger(config):
    config.world.battery_start = 0.2
    config.brain.battery_low = 0.3
    config.brain.battery_full = 0.5

    async def go():
        rt = await booted(config)
        await rt.run(60)
        await rt.shutdown()
        return rt
    rt = run(go())
    states = [t.to for t in rt.state.history]
    assert SystemState.CHARGING in states
    assert rt.report()["battery"]["level"] > 0.2


def test_estop_drill_scenario():
    async def go():
        rt = await booted(load_scenario("estop_drill"))
        await rt.run(12)
        assert rt.state.state == SystemState.ESTOP
        assert rt.bus.latest_payload("motion/cmd_applied") == {"linear": 0.0, "angular": 0.0}
        await rt.run(10)
        assert rt.state.state == SystemState.ACTIVE
        await rt.shutdown()
        return rt
    rt = run(go())
    actions = [e.action for e in rt.safety.audit.entries]
    assert "estop.engage" in actions and "estop.reset" in actions


def test_estop_reset_requires_confirmation(config):
    async def go():
        rt = await booted(config)
        await rt.bus.publish("safety/estop", {"engage": True, "reason": "test"})
        await rt.run(1)
        assert rt.safety.estop.engaged
        await rt.bus.publish("safety/estop", {"engage": False})          # no confirmation
        await rt.run(1)
        assert rt.safety.estop.engaged
        await rt.bus.publish("safety/estop", {"engage": False, "confirmed": True})
        await rt.run(1)
        assert not rt.safety.estop.engaged
        await rt.shutdown()
    run(go())


def test_sensor_fault_stops_robot_and_recovers():
    async def go():
        rt = await booted(load_scenario("sensor_fault"))
        await rt.run(7)
        assert rt.bus.latest_payload("motion/cmd_applied")["linear"] == 0.0
        assert rt.scheduler.get("perception").state.value in ("fault", "ready", "running")
        await rt.run(8)
        assert rt.scheduler.get("perception").state.value == "running"
        assert rt.scheduler.get("perception").stats.restarts >= 1
        await rt.shutdown()
        return rt
    rt = run(go())
    assert any(e.action == "violation.perception_stale" for e in rt.safety.audit.entries)
    assert rt.report()["world"]["collisions"] == 0


def test_voice_commands_drive_the_robot():
    async def go():
        rt = await booted(load_scenario("voice"))
        arrived = []
        rt.bus.subscribe("nav/arrived", lambda m: arrived.append(m.payload["name"]))
        await rt.run(50)
        assert [a for a in arrived if a != "frontier"] == ["user", "charger"]   # idle robot explores in between
        assert rt.ctx.extras["memory"].working.get("owner") == "ijtihad"
        assert rt.scheduler.get("language").understood == 4
        await rt.shutdown()
    run(go())


def test_dashboard_http_api(config):
    config.dashboard.enabled = True
    config.dashboard.port = 0

    async def go():
        rt = await booted(config)
        await rt.run(2)
        url = rt.dashboard.url
        html = urllib.request.urlopen(url + "/").read().decode()
        assert "Cyberdyne" in html
        state = json.loads(urllib.request.urlopen(url + "/api/state").read())
        assert state["state"] in ("active", "idle") and state["world"]["robot"]
        req = urllib.request.Request(url + "/api/say", data=json.dumps({"text": "stop"}).encode(),
                                     headers={"Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req).read())["ok"]
        await rt.run(2)
        assert rt.safety.estop.engaged
        await rt.shutdown()
    run(go())


def test_every_module_keeps_the_base_describe_contract():
    """Module.describe() feeds the dashboard's module table; subclasses must not override it."""
    from cyberdyne.kernel.module import Module
    from cyberdyne.sim.scenario import load_scenario
    rt = Runtime(load_scenario("chores"))
    for m in rt.scheduler.modules:
        assert type(m).describe is Module.describe, f"{m.name} overrides describe()"
        d = m.describe()
        assert {"name", "state", "rate_hz", "stats"} <= set(d) and "ticks" in d["stats"]
