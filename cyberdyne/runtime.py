"""Runtime: wires every layer together from a RobotConfig.

    rt = Runtime(config)
    await rt.boot()          # devices, self-test, modules, state -> IDLE
    await rt.run(duration)   # scheduler loop
    await rt.shutdown()
"""
from __future__ import annotations

import logging

from .cognition.brain import Brain
from .hal.registry import DeviceRegistry
from .hal.virtual import build_virtual_devices
from .kernel.bus import MessageBus
from .kernel.clock import Clock, SimClock, WallClock
from .kernel.config import RobotConfig
from .kernel.context import Context
from .kernel.scheduler import Scheduler
from .kernel.state import StateMachine, SystemState
from .kernel.watchdog import Watchdog
from .language.module import LanguageModule
from .memory.module import MemoryModule
from .motion.controller import MotionController
from .observability.dashboard import Dashboard
from .observability.telemetry import Telemetry
from .perception.range import RangePerception
from .perception.sensors import SensorHub
from .safety.core import SafetyCore, SafetyGate
from .sim.scenario import ScenarioEvents
from .sim.world import Obstacle, Pose, World
from .skills.runner import SkillRunner
from .world_model.module import WorldModel

log = logging.getLogger("cyberdyne.runtime")


class SimStepper:
    """Advances the physics world in lock-step with the kernel clock."""
    from .kernel.module import Module as _M

    class Module(_M):
        name = "sim_physics"
        rate_hz = 100.0
        priority = 2

        def __init__(self, world: World) -> None:
            super().__init__()
            self.world = world

        async def setup(self, ctx) -> None:
            self.ctx = ctx
            self._sub = ctx.bus.subscribe("sim/inject_fault", self._on_inject, name="sim.inject")

        async def teardown(self) -> None:
            self.ctx.bus.unsubscribe(self._sub)

        def _on_inject(self, msg) -> None:
            """Chaos hook: {"device": "range", "enabled": bool}."""
            p = msg.payload or {}
            for dev in self.ctx.devices.all():
                if dev.kind.value == p.get("device") and hasattr(dev, "fault_injected"):
                    dev.fault_injected = bool(p.get("enabled", True))

        async def tick(self, dt: float) -> None:
            self.world.step(dt)
            if self.world.last_collision:
                await self.ctx.bus.publish("sim/collision", {"count": self.world.collisions,
                                                             "pose": self.world.robot.to_dict()},
                                           source=self.name)


class Runtime:
    def __init__(self, config: RobotConfig | None = None, clock: Clock | None = None) -> None:
        self.config = config or RobotConfig()
        k = self.config.kernel
        if clock is None:
            clock = SimClock(realtime_factor=k.realtime_factor) if k.mode == "sim" else WallClock()
        self.clock = clock
        self.bus = MessageBus(clock, history=k.history)
        self.state = StateMachine(self.bus)
        self.devices = DeviceRegistry()
        self.safety = SafetyCore(self.config.safety)
        self.world: World | None = None
        if k.mode == "sim":
            w = self.config.world
            self.world = World(w.width, w.height, [Obstacle(**o) for o in w.obstacles],
                               Pose(**w.robot_start), charger=(w.charger["x"], w.charger["y"]))
            build_virtual_devices(self.world, w, self.devices)
        else:  # pragma: no cover - real hardware drivers land in a later phase
            raise NotImplementedError("real hardware backend not implemented yet; use mode='sim'")
        self.ctx = Context(clock, self.bus, self.config, self.state, self.devices, self.safety, self.world)
        self.scheduler = Scheduler(clock, self.bus, self.ctx)
        self._build_modules()
        self.booted = False

    def _build_modules(self) -> None:
        self.watchdog = Watchdog()
        self.telemetry = Telemetry()
        self.brain = Brain()
        self.world_model = WorldModel()
        mods = [SafetyGate(), self.watchdog, SensorHub(), RangePerception(), self.world_model,
                MotionController(), self.brain, LanguageModule(), SkillRunner(), MemoryModule(),
                self.telemetry]
        if self.world is not None:
            mods.append(SimStepper.Module(self.world))
        if self.config.events:
            mods.append(ScenarioEvents())
        self.dashboard: Dashboard | None = None
        if self.config.dashboard.enabled:
            self.dashboard = Dashboard(self.config.dashboard.host, self.config.dashboard.port)
            mods.append(self.dashboard)
        self.scheduler.register(*mods)
        self.watchdog.attach(self.scheduler)
        self.telemetry.attach(self.scheduler)
        self.ctx.extras["world_model"] = self.world_model

    async def boot(self) -> dict[str, tuple[bool, str]]:
        self.safety.audit.record(self.clock.now(), "runtime", "boot", robot=self.config.name)
        await self.devices.open_all()
        await self.state.transition(SystemState.DIAGNOSTIC, "boot complete")
        results = await self.devices.self_test_all()
        await self.bus.publish("kernel/diagnostic", {k: {"ok": ok, "msg": m} for k, (ok, m) in results.items()},
                               source="runtime")
        await self.scheduler.setup_all()
        failed = [k for k, (ok, _) in results.items() if not ok]
        if failed:
            await self.safety.estop.engage(f"self-test failed: {', '.join(failed)}")
            await self.state.transition(SystemState.ESTOP, "diagnostic failure")
        else:
            await self.state.transition(SystemState.IDLE, "diagnostic ok")
        self.booted = True
        return results

    async def run(self, duration: float | None = None):
        if not self.booted:
            await self.boot()
        return await self.scheduler.run(duration)

    async def shutdown(self) -> None:
        await self.scheduler.teardown_all()
        await self.devices.close_all()
        if self.state.can(SystemState.SHUTDOWN):
            await self.state.transition(SystemState.SHUTDOWN, "runtime shutdown")
        self.safety.audit.record(self.clock.now(), "runtime", "shutdown")

    async def say(self, text: str, confirmed: bool = False) -> None:
        await self.bus.publish("language/utterance", {"text": text, "speaker": "api", "confirmed": confirmed},
                               source="runtime")

    def report(self) -> dict:
        ok, bad = self.safety.audit.verify()
        return {"robot": self.config.name, "sim_time": round(self.clock.now(), 3),
                "state": self.state.state.value,
                "battery": self.bus.latest_payload("sensor/battery"),
                "pose": self.bus.latest_payload("sensor/odometry"),
                "brain": self.bus.latest_payload("brain/state"),
                "world": self.world.to_dict() if self.world else None,
                "estop": self.safety.describe()["estop"],
                "audit": {"entries": len(self.safety.audit), "chain_ok": ok, "first_bad": bad},
                "bus": {"published": self.bus.stats.published, "errors": self.bus.stats.handler_errors},
                "scheduler": {"frames": self.scheduler.stats.frames, "ticks": self.scheduler.stats.ticks,
                              "faults": self.scheduler.stats.faults},
                "modules": {m.name: m.state.value for m in self.scheduler.modules}}
