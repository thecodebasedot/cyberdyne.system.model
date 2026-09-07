import pytest

from cyberdyne.kernel.bus import MessageBus
from cyberdyne.kernel.clock import SimClock
from cyberdyne.kernel.config import RobotConfig
from cyberdyne.kernel.context import Context
from cyberdyne.kernel.module import Module, ModuleState
from cyberdyne.kernel.scheduler import Scheduler
from cyberdyne.kernel.state import IllegalTransition, StateMachine, SystemState
from cyberdyne.kernel.watchdog import Watchdog
from cyberdyne.safety.core import SafetyCore
from tests.conftest import run


def _ctx(clock, bus):
    cfg = RobotConfig()
    return Context(clock, bus, cfg, StateMachine(bus), None, SafetyCore(cfg.safety))


def test_bus_wildcards_and_isolation():
    bus = MessageBus(SimClock())
    seen = []
    bus.subscribe("a/*", lambda m: seen.append(m.topic))

    def boom(m):
        raise RuntimeError("bad handler")
    bus.subscribe("a/x", boom)

    async def go():
        await bus.publish("a/x", 1)
        await bus.publish("b/x", 2)
    run(go())
    assert seen == ["a/x"]
    assert bus.stats.handler_errors == 1
    assert bus.latest("kernel/fault").payload["kind"] == "handler"
    assert bus.latest_payload("b/x") == 2


def test_state_machine_whitelist():
    bus = MessageBus(SimClock())
    sm = StateMachine(bus)

    async def go():
        await sm.transition(SystemState.DIAGNOSTIC, "boot")
        await sm.transition(SystemState.IDLE, "ok")
        with pytest.raises(IllegalTransition):
            await sm.transition(SystemState.BOOT, "backwards")
        await sm.transition(SystemState.ESTOP, "test")
        assert not sm.can(SystemState.ACTIVE)
    run(go())
    assert [t.to for t in sm.history] == [SystemState.DIAGNOSTIC, SystemState.IDLE, SystemState.ESTOP]
    assert bus.latest("kernel/state").payload["to"] == "estop"


class Counter(Module):
    name = "counter"
    rate_hz = 10.0

    def __init__(self):
        super().__init__()
        self.n = 0

    async def tick(self, dt):
        self.n += 1


class Flaky(Module):
    name = "flaky"
    rate_hz = 10.0
    max_faults = 2

    def __init__(self):
        super().__init__()
        self.calls = 0
        self.setups = 0

    async def setup(self, ctx):
        self.setups += 1

    async def tick(self, dt):
        self.calls += 1
        if self.setups == 1:
            raise RuntimeError("first life always fails")


def test_scheduler_rates_are_deterministic():
    clock = SimClock()
    bus = MessageBus(clock)
    sched = Scheduler(clock, bus, _ctx(clock, bus))
    c = Counter()
    sched.register(c)

    async def go():
        await sched.setup_all()
        await sched.run(duration=2.0)
    run(go())
    assert c.n == 20
    assert c.stats.ticks == 20 and c.stats.faults == 0


def test_scheduler_faults_and_watchdog_restart():
    clock = SimClock()
    bus = MessageBus(clock)
    ctx = _ctx(clock, bus)
    sched = Scheduler(clock, bus, ctx)
    f, wd = Flaky(), Watchdog(cooldown=0.5)
    wd.attach(sched)
    sched.register(f, wd)

    async def go():
        await sched.setup_all()
        await sched.run(duration=3.0)
    run(go())
    assert f.setups == 2                      # restarted exactly once
    assert f.state == ModuleState.RUNNING
    assert f.stats.restarts == 1 and f.stats.faults == 2
    assert bus.latest("kernel/module_restart").payload["ok"] is True
    ok, _ = ctx.safety.audit.verify()
    assert ok and any(e.action == "module.restart" for e in ctx.safety.audit.entries)


def test_critical_module_fault_engages_estop():
    clock = SimClock()
    bus = MessageBus(clock)
    ctx = _ctx(clock, bus)
    sched = Scheduler(clock, bus, ctx)

    class Critical(Flaky):
        name = "critical"
        critical = True
    sched.register(Critical())

    async def go():
        await sched.setup_all()
        await sched.run(duration=1.0)
    run(go())
    assert ctx.safety.estop.engaged
    assert "critical" in ctx.safety.estop.reason
