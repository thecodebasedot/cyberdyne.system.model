"""Runtime: wires every layer together from a RobotConfig.

    rt = Runtime(config)
    await rt.boot()          # devices, self-test, modules, state -> IDLE
    await rt.run(duration)   # scheduler loop
    await rt.shutdown()
"""
from __future__ import annotations

import logging

from .cognition.attention import AttentionModule
from .cognition.brain import Brain
from .cognition.drives import DrivesModule
from .cognition.llm import LLMBackend, build_backend
from .cognition.llm_planner import LLMPlanner
from .hal.registry import DeviceRegistry
from .hal.serial import build_serial_devices
from .hal.virtual import build_virtual_devices
from .home.hub import build_hub
from .kernel.bus import MessageBus
from .kernel.clock import Clock, SimClock, WallClock
from .kernel.config import RobotConfig
from .kernel.context import Context
from .kernel.governor import Governor
from .kernel.reload import HotReloader
from .kernel.scheduler import Scheduler
from .kernel.state import StateMachine, SystemState
from .kernel.watchdog import Watchdog
from .language.llm_interpreter import LLMInterpreter
from .language.module import LanguageModule
from .language.voice import VoiceModule
from .learning.recorder import DemoRecorder
from .learning.user_model import UserModel, UserModelModule
from .memory.module import MemoryModule
from .memory.persist import Store
from .motion.controller import MotionController
from .observability.dashboard import Dashboard
from .observability.recording import BusRecorder
from .observability.telemetry import Telemetry
from .ops.release import OpsModule
from .perception.range import RangePerception
from .perception.sensors import SensorHub
from .perception.vision import VisionPerception
from .safety.core import SafetyCore, SafetyGate
from .sim.scenario import ScenarioEvents
from .sim.world import Actor, Obstacle, Pose, World
from .skills.authoring import MacroSkill
from .skills.registry import SkillRegistry
from .skills.runner import SkillRunner
from .social.module import SocialModule
from .tasks.routines import RoutineModule
from .tasks.runner import TaskRunner
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
            self._sub2 = ctx.bus.subscribe("sim/hear", self._on_hear, name="sim.hear")

        async def teardown(self) -> None:
            self.ctx.bus.unsubscribe(self._sub)
            self.ctx.bus.unsubscribe(self._sub2)

        def _on_inject(self, msg) -> None:
            """Chaos hook: {"device": "range", "enabled": bool}."""
            p = msg.payload or {}
            for dev in self.ctx.devices.all():
                if dev.kind.value == p.get("device") and hasattr(dev, "fault_injected"):
                    dev.fault_injected = bool(p.get("enabled", True))

        def _on_hear(self, msg) -> None:
            """Scenario hook: {"text": ..., "signature": ...} -> the virtual microphone."""
            p = msg.payload or {}
            for dev in self.ctx.devices.all():
                if hasattr(dev, "inject"):
                    dev.inject(str(p.get("text", "")), str(p.get("signature", "")), float(p.get("loudness", 1.0)))

        async def tick(self, dt: float) -> None:
            self.world.step(dt)
            if self.world.last_collision:
                await self.ctx.bus.publish("sim/collision", {"count": self.world.collisions,
                                                             "pose": self.world.robot.to_dict()},
                                           source=self.name)


class LoopbackStepper(SimStepper._M):
    """Advances the fake firmware in lock-step with the kernel clock (serial mode + loopback)."""
    name = "loopback_firmware"
    rate_hz = 100.0
    priority = 2

    def __init__(self, transport) -> None:
        super().__init__()
        self.transport = transport

    async def tick(self, dt: float) -> None:
        self.transport.advance(dt)


class Runtime:
    def __init__(self, config: RobotConfig | None = None, clock: Clock | None = None,
                 llm: LLMBackend | None = None) -> None:
        self.config = config or RobotConfig()
        b = self.config.brain
        self.llm = llm if llm is not None else build_backend(b.llm, b.model, b.effort)
        k = self.config.kernel
        if clock is None:
            clock = SimClock(realtime_factor=k.realtime_factor) if k.mode == "sim" else WallClock()
        self.clock = clock
        self.bus = MessageBus(clock, history=k.history, strict=k.strict_bus)
        self.recorder = BusRecorder(self.bus, k.record) if k.record else None
        self.state = StateMachine(self.bus)
        self.devices = DeviceRegistry()
        self.safety = SafetyCore(self.config.safety)
        self.world: World | None = None
        self.bridge = None
        self.bullet = None
        if k.mode == "sim":
            w = self.config.world
            self.world = World(width=w.width, height=w.height, obstacles=[Obstacle(**o) for o in w.obstacles],
                               actors=[Actor(a["id"], a.get("kind", "person"), a["x"], a["y"],
                                             a.get("signature", ""), [tuple(p) for p in a.get("route", [])],
                                             a.get("speed", 0.5), a.get("radius", 0.3), a.get("active_from", 0.0))
                                       for a in w.actors],
                               robot=Pose(**w.robot_start), charger=(w.charger["x"], w.charger["y"]))
            build_virtual_devices(self.world, w, self.devices)
        elif k.mode == "bullet":
            from .hal.bullet import build_bullet_devices
            w = self.config.world
            actors = [Actor(a["id"], a.get("kind", "person"), a["x"], a["y"], a.get("signature", ""),
                            [tuple(pt) for pt in a.get("route", [])], a.get("speed", 0.5), a.get("radius", 0.3),
                            a.get("active_from", 0.0)) for a in w.actors]
            self.bullet = build_bullet_devices(w, actors, self.devices)
            self.world = self.bullet.mirror              # 2D mirror for dashboard / mental sim
        elif k.mode == "serial":
            self.bridge = build_serial_devices(self.config.hardware, self.devices)
        elif k.mode == "rpi":
            from .hal.rpi import build_rpi_devices
            h = self.config.hardware
            self.bridge = build_rpi_devices(h, self.devices, None if h.camera_index < 0 else h.camera_index,
                                            h.vosk_model or None, h.tts)
        else:
            raise ValueError(f"unknown kernel mode {k.mode!r}; use 'sim', 'bullet', 'serial' or 'rpi'")
        self.ctx = Context(clock, self.bus, self.config, self.state, self.devices, self.safety, self.world)
        self.scheduler = Scheduler(clock, self.bus, self.ctx)
        self._build_modules()
        self.booted = False

    def _build_modules(self) -> None:
        self.watchdog = Watchdog()
        self.governor = Governor()
        self.reloader = HotReloader()
        self.telemetry = Telemetry()
        self.world_model = WorldModel()
        registry = SkillRegistry()
        registry.load_builtin()
        self.store = Store(self.config.learning.data_dir) if self.config.learning.data_dir else None
        user_model = UserModel.from_dict(self.store.load("user_model", {})) if self.store else UserModel()
        if self.store:
            for m in self.store.load("macros", {}).values():
                registry.register(MacroSkill(m["name"], m["description"], m["steps"], m["params"]))
        self.user_model = UserModelModule(user_model)
        self.tasks = TaskRunner()
        planner = LLMPlanner(self.llm, effort=self.config.brain.effort) if self.llm else None
        interpreter = LLMInterpreter(self.llm, registry.describe()) if self.llm else None
        self.brain = Brain(planner)
        # SkillRunner registers before Brain so the constitution sees the skill list at setup.
        mods = [SafetyGate(), self.watchdog, self.governor, self.reloader, SensorHub(), RangePerception(),
                self.world_model, MotionController(), SkillRunner(registry), self.tasks, DrivesModule(),
                AttentionModule(), self.brain,
                LanguageModule(interpreter), RoutineModule(), MemoryModule(), self.user_model, DemoRecorder(),
                OpsModule(), self.telemetry]
        from .hal.interfaces import DeviceKind
        if self.devices.has(DeviceKind.CAMERA):
            mods += [VisionPerception(), SocialModule()]
        if self.devices.has(DeviceKind.MIC) or self.devices.has(DeviceKind.SPEAKER):
            mods.append(VoiceModule())
        if self.bullet is not None:
            from .hal.bullet.backend import BulletStepper
            mods.append(BulletStepper(self.bullet))
        elif self.world is not None:
            mods.append(SimStepper.Module(self.world))
        if self.bridge is not None and hasattr(self.bridge.t, "advance"):
            mods.append(LoopbackStepper(self.bridge.t))
        if self.config.events:
            mods.append(ScenarioEvents())
        self.dashboard: Dashboard | None = None
        if self.config.dashboard.enabled:
            self.dashboard = Dashboard(self.config.dashboard.host, self.config.dashboard.port)
            mods.append(self.dashboard)
        self.scheduler.register(*mods)
        self.watchdog.attach(self.scheduler)
        self.governor.attach(self.scheduler)
        self.reloader.attach(self.scheduler)
        self.telemetry.attach(self.scheduler)
        self.ctx.extras["world_model"] = self.world_model
        self.ctx.extras["llm"] = self.llm
        self.ctx.extras["home"] = build_hub(self.config.home)
        self.ctx.extras["store"] = self.store

    async def boot(self) -> dict[str, tuple[bool, str]]:
        self.safety.audit.record(self.clock.now(), "runtime", "boot", robot=self.config.name)
        await self.devices.open_all()
        await self.state.transition(SystemState.DIAGNOSTIC, "boot complete")
        results = await self.devices.self_test_all()
        await self.bus.publish("kernel/diagnostic", {k: {"ok": ok, "msg": m} for k, (ok, m) in results.items()},
                               source="runtime")
        await self.scheduler.setup_all()
        if self.store:
            from .tasks.model import TaskStep
            for name, steps in self.store.load("tasks", {}).items():
                self.tasks.define(name, [TaskStep.from_dict(x) for x in steps])
            mem = self.ctx.extras.get("memory")
            for f in self.store.load("facts", []):
                mem.semantic.add(f["subject"], f["predicate"], f["object"], f.get("source", "store"), f.get("ts", 0.0))
        critical = {d.device_id for d in self.devices.all() if d.kind.value in ("drive", "range", "battery")}
        failed = [k for k, (ok, _) in results.items() if not ok and k in critical]
        degraded = [k for k, (ok, _) in results.items() if not ok and k not in critical]
        if degraded:
            log.warning("degraded peripherals: %s", degraded)
            await self.bus.publish("kernel/degraded", {"devices": degraded}, source="runtime")
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
        if self.store and "memory" in self.ctx.extras:
            self.store.snapshot(self.ctx.extras["memory"], self.user_model.model, self.tasks.describe_library())
        await self.scheduler.teardown_all()
        await self.devices.close_all()
        if self.recorder:
            self.recorder.close()
        if self.bullet is not None:
            self.bullet.close()
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
                "room": self.world_model.current_room,
                "tasks": {"completed": self.tasks.completed, "failed": self.tasks.failed,
                          "library": sorted(self.tasks.library)},
                "estop": self.safety.describe()["estop"],
                "audit": {"entries": len(self.safety.audit), "chain_ok": ok, "first_bad": bad},
                "bus": {"published": self.bus.stats.published, "errors": self.bus.stats.handler_errors},
                "scheduler": {"frames": self.scheduler.stats.frames, "ticks": self.scheduler.stats.ticks,
                              "faults": self.scheduler.stats.faults},
                "llm": self.llm.describe() if self.llm else None,
                "schema_errors": self.bus.schema_errors,
                "recorded": self.recorder.count if self.recorder else None,
                "decisions": [d.to_dict() for d in self.brain.council.history[-5:]] if self.brain.council else [],
                "modules": {m.name: m.state.value for m in self.scheduler.modules}}
