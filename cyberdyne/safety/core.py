"""SafetyCore bundles the immutable safety primitives; SafetyGate is the
one module allowed to write to the drive base.

Data flow:   motion/cmd  --> SafetyGate --> DriveBase
                              |  clamps to envelope
                              |  zeroes on e-stop / critical battery
                              +-> motion/cmd_applied, safety/violation
"""
from __future__ import annotations

from ..hal.interfaces import DeviceKind, DriveBase
from ..kernel.config import SafetyConfig
from ..kernel.context import Context
from ..kernel.module import Module
from .audit import AuditLog
from .envelope import SafetyEnvelope, Violation
from .estop import EStop
from .permissions import PermissionPolicy


class SafetyCore:
    def __init__(self, cfg: SafetyConfig) -> None:
        self.cfg = cfg
        self.envelope = SafetyEnvelope(cfg)
        self.estop = EStop()
        self.permissions = PermissionPolicy(cfg.permissions, cfg.guest_max_tier, cfg.unknown_max_tier)
        self.audit = AuditLog()

    def describe(self) -> dict:
        return {"estop": {"engaged": self.estop.engaged, "reason": self.estop.reason,
                          "count": self.estop.count},
                "limits": {"max_linear": self.cfg.max_linear, "max_angular": self.cfg.max_angular,
                           "obstacle_stop_distance": self.cfg.obstacle_stop_distance},
                "keep_out": [z.to_dict() for z in self.envelope.keep_out],
                "audit_entries": len(self.audit)}


class SafetyGate(Module):
    name = "safety_gate"
    rate_hz = 50.0
    priority = 0
    critical = True

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.drive = ctx.devices.get(DeviceKind.DRIVE, DriveBase)
        self.core = ctx.safety
        self._requested = (0.0, 0.0)
        self._applied = (0.0, 0.0)
        self.violations = 0
        self._active_rules: dict[str, float] = {}
        self._sub = ctx.bus.subscribe("motion/cmd", self._on_cmd, name="safety_gate.cmd")
        self._sub_estop = ctx.bus.subscribe("safety/estop", self._on_estop, name="safety_gate.estop")
        self.core.estop.on_engage(self._engaged)
        self.core.estop.on_reset(self._reset)

    async def teardown(self) -> None:
        if self.ctx:
            self.ctx.bus.unsubscribe(self._sub)
            self.ctx.bus.unsubscribe(self._sub_estop)
        await self.drive.stop()

    def _on_cmd(self, msg) -> None:
        p = msg.payload or {}
        self._requested = (float(p.get("linear", 0.0)), float(p.get("angular", 0.0)))

    async def _on_estop(self, msg) -> None:
        p = msg.payload or {}
        if p.get("engage", True):
            await self.core.estop.engage(p.get("reason", "requested"))
        else:
            self.core.permissions.check("skill.estop_reset", confirmed=bool(p.get("confirmed")))
            await self.core.estop.reset()

    async def _engaged(self, reason: str) -> None:
        self._requested = (0.0, 0.0)
        await self.drive.stop()
        self.core.audit.record(self.ctx.now, "safety", "estop.engage", reason=reason)
        await self.ctx.bus.publish("safety/estop_state", {"engaged": True, "reason": reason},
                                   source=self.name)

    async def _reset(self) -> None:
        self.core.audit.record(self.ctx.now, "safety", "estop.reset")
        await self.ctx.bus.publish("safety/estop_state", {"engaged": False, "reason": ""},
                                   source=self.name)

    async def tick(self, dt: float) -> None:
        lin, ang = self._requested
        bus = self.ctx.bus
        if self.core.estop.engaged:
            lin, ang = 0.0, 0.0
        batt = bus.latest_payload("sensor/battery")
        if batt and batt["level"] <= self.core.cfg.battery_critical and not batt.get("charging"):
            lin = min(lin, 0.0)
        odom = bus.latest_payload("sensor/odometry")
        clr_msg = bus.latest("perception/front_clearance")
        clearance = clr_msg.payload if clr_msg else None
        lin, ang, viol = self.core.envelope.clamp(lin, ang, pose=odom, front_clearance=clearance)
        # Never drive forward on perception that has gone quiet: the world may have changed.
        if lin > 0 and (clr_msg is None or self.ctx.now - clr_msg.ts > self.core.cfg.perception_stale):
            age = None if clr_msg is None else round(self.ctx.now - clr_msg.ts, 2)
            viol.append(Violation("perception_stale", f"age={age}"))
            lin = 0.0
        rules = {v.rule for v in viol}
        if rules != set(self._active_rules):
            # audit + publish on change only, so a held violation is one episode, not 50 Hz of noise
            for v in viol:
                if v.rule not in self._active_rules:
                    self.core.audit.record(self.ctx.now, "safety", f"violation.{v.rule}", detail=v.detail)
            await bus.publish("safety/violation", {"active": [v.__dict__ for v in viol],
                                                   "cleared": sorted(set(self._active_rules) - rules)},
                              source=self.name)
        self.violations += len(viol)
        self._active_rules = {v.rule: self.ctx.now for v in viol}
        await self.drive.set_velocity(lin, ang)     # every tick: the HAL is the source of truth
        if (lin, ang) != self._applied:
            self._applied = (lin, ang)
            await bus.publish("motion/cmd_applied", {"linear": lin, "angular": ang}, source=self.name)
