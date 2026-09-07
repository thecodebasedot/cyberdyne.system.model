"""Over-the-air updates.

A ``Bundle`` is a versioned, hashed set of *behavioural* config: routines,
library tasks, macro skills, permission overrides. It never touches the
safety core (``safety.*`` keys are rejected) or code. Applying a bundle:

    validate -> stage -> activate -> health window -> commit | auto-rollback

During the health window any module fault, e-stop or task failure rolls
the bundle back to the previous one. ``rollback()`` can also be called by
hand. Every step is audited.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ..kernel.context import Context
from ..kernel.module import Module
from ..skills.authoring import MacroSkill, validate_macro
from ..tasks.model import TaskStep
from ..tasks.routines import Routine

FORBIDDEN_KEYS = ("safety", "kernel", "hardware")


@dataclass
class Bundle:
    version: str
    routines: list[dict] = field(default_factory=list)
    tasks: dict[str, list[dict]] = field(default_factory=dict)
    macros: list[dict] = field(default_factory=list)
    permissions: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    @property
    def hash(self) -> str:
        body = json.dumps({"version": self.version, "routines": self.routines, "tasks": self.tasks,
                           "macros": self.macros, "permissions": self.permissions}, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()[:16]

    @classmethod
    def from_dict(cls, d: dict) -> Bundle:
        bad = [k for k in d if k in FORBIDDEN_KEYS]
        if bad:
            raise ValueError(f"bundle may not carry {bad}")
        return cls(str(d["version"]), list(d.get("routines", [])), dict(d.get("tasks", {})),
                   list(d.get("macros", [])), dict(d.get("permissions", {})), str(d.get("notes", "")))

    def to_dict(self) -> dict:
        return {"version": self.version, "hash": self.hash, "routines": self.routines, "tasks": self.tasks,
                "macros": self.macros, "permissions": self.permissions, "notes": self.notes}


class ReleaseManager:
    def __init__(self, ctx: Context, health_window: float = 10.0) -> None:
        self.ctx = ctx
        self.health_window = health_window
        self.active: Bundle | None = None
        self.previous: Bundle | None = None
        self.pending_until: float | None = None
        self.history: list[dict] = []

    def validate(self, b: Bundle) -> list[str]:
        problems = []
        for k in b.permissions:
            if k.startswith("safety.") or k == "self.modify":
                problems.append(f"permission {k} may not be changed by a bundle")
        brain = self.ctx.extras.get("brain")
        registry = self.ctx.extras.get("skills")
        constitution = brain.council.safety.constitution if brain and brain.council else None
        for m in b.macros:
            if constitution and registry:
                known = set(registry.names()) - {m.get("name")}
                problems += [f"macro {m.get('name')}: {p}" for p in validate_macro(m, constitution, known)]
        for name, steps in b.tasks.items():
            for s in steps:
                try:
                    TaskStep.from_dict(s)
                except (KeyError, ValueError, TypeError) as exc:
                    problems.append(f"task {name}: bad step {s}: {exc}")
        for r in b.routines:
            if "name" not in r or not (r.get("every") or r.get("at")):
                problems.append(f"routine {r}: needs name and every/at")
        return problems

    async def apply(self, b: Bundle) -> list[str]:
        problems = self.validate(b)
        now = self.ctx.now
        if problems:
            self.ctx.safety.audit.record(now, "ops", "update.rejected", version=b.version, problems=problems)
            await self.ctx.bus.publish("ops/rejected", {"version": b.version, "problems": problems}, source="ops")
            return problems
        self.previous, self.active = self.active, b
        self._activate(b)
        self.pending_until = now + self.health_window
        self.history.append({"at": now, "version": b.version, "hash": b.hash, "event": "activated"})
        self.ctx.safety.audit.record(now, "ops", "update.activated", version=b.version, hash=b.hash)
        await self.ctx.bus.publish("ops/activated", b.to_dict(), source="ops")
        return []

    def _activate(self, b: Bundle | None) -> None:
        routines = self.ctx.extras.get("routines")
        tasks = self.ctx.extras.get("tasks")
        registry = self.ctx.extras.get("skills")
        # remove what the previously active bundle installed
        if routines:
            routines.routines = [r for r in routines.routines if r.origin != "bundle"]
        if tasks:
            for name in [n for n, s in tasks.library.items() if getattr(s, "_bundle", False)]:
                del tasks.library[name]
        if registry:
            for name in [n for n in registry.names() if getattr(registry.get(n), "_bundle", False)]:
                del registry._skills[name]
        if b is None:
            return
        if routines:
            for r in b.routines:
                routines.add(Routine(r["name"], r.get("every"), r.get("at"), None, r.get("goal"), r.get("task"),
                                     r.get("speak"), origin="bundle"))
        if tasks:
            for name, steps in b.tasks.items():
                lst = [TaskStep.from_dict(s) for s in steps]
                lst = _Tagged(lst)
                tasks.library[name] = lst
        if registry:
            for m in b.macros:
                macro = MacroSkill(m["name"], m.get("description", ""), m["steps"], m.get("params", {}))
                macro._bundle = True
                registry.register(macro)
        for k, v in b.permissions.items():
            self.ctx.safety.permissions.rules[k] = type(self.ctx.safety.permissions.tier(k))(v)

    async def rollback(self, reason: str = "manual") -> bool:
        if self.active is None:
            return False
        rolled = self.active
        self.active, self.previous = self.previous, None
        self._activate(self.active)
        self.pending_until = None
        self.history.append({"at": self.ctx.now, "version": rolled.version, "event": f"rolled back: {reason}"})
        self.ctx.safety.audit.record(self.ctx.now, "ops", "update.rollback", version=rolled.version, reason=reason)
        await self.ctx.bus.publish("ops/rolled_back", {"version": rolled.version, "reason": reason,
                                                       "now": self.active.version if self.active else None},
                                   source="ops")
        return True

    async def commit(self) -> None:
        self.pending_until = None
        self.previous = None
        self.history.append({"at": self.ctx.now, "version": self.active.version, "event": "committed"})
        self.ctx.safety.audit.record(self.ctx.now, "ops", "update.committed", version=self.active.version)
        await self.ctx.bus.publish("ops/committed", self.active.to_dict(), source="ops")

    def describe(self) -> dict:
        return {"active": self.active.to_dict() if self.active else None,
                "previous": self.previous.version if self.previous else None,
                "health_pending": self.pending_until, "history": self.history[-10:]}


class _Tagged(list):
    _bundle = True


class OpsModule(Module):
    name = "ops"
    rate_hz = 2.0
    priority = 85

    def __init__(self, health_window: float = 10.0) -> None:
        super().__init__()
        self.health_window = health_window

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.manager = ReleaseManager(ctx, self.health_window)
        self._unhealthy: str | None = None
        self._subs = [ctx.bus.subscribe("ops/update", self._on_update, name="ops.update"),
                      ctx.bus.subscribe("ops/rollback", self._on_rollback, name="ops.rollback")]
        for t in ("kernel/module_fault", "task/failed", "safety/estop_state"):
            self._subs.append(ctx.bus.subscribe(t, self._on_health, name=f"ops.{t}"))
        ctx.extras["ops"] = self.manager

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    async def _on_update(self, msg) -> None:
        try:
            bundle = Bundle.from_dict(msg.payload or {})
        except (ValueError, KeyError) as exc:
            self.ctx.safety.audit.record(self.ctx.now, "ops", "update.rejected", problems=[str(exc)])
            await self.ctx.bus.publish("ops/rejected", {"problems": [str(exc)]}, source=self.name)
            return
        await self.manager.apply(bundle)

    async def _on_rollback(self, msg) -> None:
        await self.manager.rollback(str((msg.payload or {}).get("reason", "manual")))

    def _on_health(self, msg) -> None:
        if self.manager.pending_until is not None:
            if msg.topic == "safety/estop_state" and not msg.payload.get("engaged"):
                return
            self._unhealthy = f"{msg.topic}: {msg.payload}"

    async def tick(self, dt: float) -> None:
        m = self.manager
        if m.pending_until is None:
            return
        if self._unhealthy:
            reason, self._unhealthy = self._unhealthy, None
            await m.rollback(f"health check failed ({reason})")
        elif self.ctx.now >= m.pending_until:
            await m.commit()
