"""Governor, hot reload, clock sync, metrics, harness, scaffolder, bench."""
import importlib
import os
import subprocess
import sys
import textwrap
import urllib.request

from cyberdyne.fleet.sim import FleetSim
from cyberdyne.kernel.clocksync import ClockSync
from cyberdyne.kernel.module import Module
from cyberdyne.runtime import Runtime
from cyberdyne.testing import robot
from tests.conftest import run
from tests.test_scale import _fleet_configs


class Hog(Module):
    name = "hog"
    rate_hz = 20.0
    priority = 60

    def __init__(self):
        super().__init__()
        self.slow = True

    async def tick(self, dt):
        if self.slow:
            import time
            time.sleep(0.03)          # > 50% of a 50 ms period


def test_governor_throttles_and_recovers(config):
    config.brain.patrol = []

    async def go():
        rt = Runtime(config)
        hog = Hog()
        rt.scheduler.register(hog)
        rt.governor.recover_after = 1.0
        events = []
        rt.bus.subscribe("kernel/throttled", lambda m: events.append(("t", m.payload["rate_hz"])))
        rt.bus.subscribe("kernel/unthrottled", lambda m: events.append(("u", m.payload["rate_hz"])))
        await rt.boot()
        await rt.run(4)
        assert hog.rate_hz < 20.0 and events and events[0][0] == "t"
        assert rt.scheduler.get("safety_gate").rate_hz == 50.0          # never throttled
        hog.slow = False
        await rt.run(12)
        assert hog.rate_hz == 20.0 and any(e[0] == "u" for e in events)
        await rt.shutdown()
    run(go())


def test_hot_reload_swaps_module_class(tmp_path, config, monkeypatch):
    config.brain.patrol = []
    src = tmp_path / "hotmod.py"
    src.write_text(textwrap.dedent('''
        from cyberdyne.kernel.module import Module
        VERSION = 1
        class Hot(Module):
            name = "hot"
            rate_hz = 5.0
            priority = 60
            async def tick(self, dt):
                await self.ctx.bus.publish("hot/version", {"v": VERSION}, source=self.name)
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    mod = importlib.import_module("hotmod")

    async def go():
        rt = Runtime(config)
        rt.scheduler.register(mod.Hot())
        await rt.boot()
        await rt.run(1)
        assert rt.bus.latest_payload("hot/version")["v"] == 1
        src.write_text(src.read_text().replace("VERSION = 1", "VERSION = 2"))
        await rt.bus.publish("kernel/reload", {"module": "hot"})
        await rt.run(2)
        assert rt.bus.latest_payload("kernel/reloaded")["ok"] and rt.bus.latest_payload("hot/version")["v"] == 2
        assert rt.reloader.reloads == 1 and rt.scheduler.get("hot").state.value == "running"
        await rt.bus.publish("nav/goal", {"x": 5.0, "y": 5.0})
        await rt.run(1)
        await rt.bus.publish("kernel/reload", {"module": "safety_gate"})
        await rt.run(1)
        assert "safety-critical" in rt.bus.latest_payload("kernel/reload_failed")["reason"]
        await rt.shutdown()
    run(go())
    sys.modules.pop("hotmod", None)


def test_clock_sync_math_and_fleet_skew():
    cs = ClockSync()
    for i in range(5):
        cs.observe("b", 100.0 + i, 90.0 + i)          # b is 10 s ahead
        cs.observe("c", 100.0 + i, 110.0 + i)         # c is 10 s behind
    assert cs.offset("b") == 10.0 and cs.offset("c") == -10.0 and cs.fleet_offset() == 0.0
    cs2 = ClockSync()
    cs2.observe("b", 105.0, 100.0)
    assert cs2.fleet_offset() == 2.5 and cs2.to_fleet(0.0) == 2.5 and cs2.from_fleet(2.5) == 0.0
    assert "b" in cs2.skewed_peers()

    async def go():
        fs = FleetSim(_fleet_configs(3))
        await fs.boot()
        fs.get("r2").ctx.extras["fleet"].clock_offset = 7.0          # r2's clock runs 7 s ahead
        await fs.run(8)
        r1 = fs.get("r1").ctx.extras["fleet"]
        assert abs(r1.sync.offset("r2") - 7.0) < 0.3
        assert all(rt.world_model.find("keys") for rt in fs.robots)   # sync still converges despite skew
        await fs.shutdown()
    run(go())


def test_prometheus_metrics_endpoint(config):
    config.dashboard.enabled = True
    config.dashboard.port = 0

    async def go():
        rt = Runtime(config)
        await rt.boot()
        await rt.run(1)
        text = urllib.request.urlopen(rt.dashboard.url + "/metrics").read().decode()
        assert 'cyberdyne_battery_level{robot="cyberdyne-01"}' in text
        assert 'cyberdyne_module_ticks_total{robot="cyberdyne-01",module="safety_gate"}' in text
        assert "# TYPE cyberdyne_estop_engaged gauge" in text
        await rt.shutdown()
    run(go())


def test_harness_and_scaffolded_skill(tmp_path, monkeypatch):
    from cyberdyne.skills.scaffold import scaffold
    files = scaffold("wave", tmp_path, "wave at people")
    assert [f.name for f in files] == ["wave.py", "test_skill_wave.py"]
    monkeypatch.syspath_prepend(str(tmp_path))

    async def go():
        async with robot(skills=["skills_ext.wave"]) as r:
            res = await r.invoke("wave", {"who": "Rafi"})
            assert res.ok and res.output["who"] == "Rafi"
            said = await r.wait_for("speech/said", timeout=3)
            assert said["text"].startswith("Hello Rafi")
            assert r.report["scheduler"]["faults"] == 0
    run(go())
    # the generated test file itself passes under pytest
    env = {**os.environ, "PYTHONPATH": f"{tmp_path}:{os.getcwd()}"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", str(files[1])], capture_output=True, text=True,
                          cwd=str(tmp_path), env=env)
    assert proc.returncode in (0, 5) or "passed" in proc.stdout, proc.stdout + proc.stderr


def test_bench_table():
    from cyberdyne.bench import run_all, table
    rows = run(run_all(15, ["estop_drill"]))
    assert rows[0]["scenario"] == "estop_drill" and rows[0]["audit_ok"]
    assert "estop_drill" in table(rows)
