"""Hot reload: replace a running module with a freshly imported class.

    kernel/reload {"module": "<name>"}  -> teardown, importlib.reload(source module),
                                          instantiate the same class name, setup, swap in place

State that must survive a reload should live on the bus or in ``ctx.extras``,
which is the rule for every module anyway. Safety-critical modules
(priority < 10) refuse to reload while the robot is moving.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys

from .context import Context
from .module import Module, ModuleState


class HotReloader(Module):
    name = "reloader"
    rate_hz = 1.0
    priority = 4

    def __init__(self) -> None:
        super().__init__()
        self._scheduler = None
        self._queue: list[str] = []
        self.reloads = 0

    def attach(self, scheduler) -> None:
        self._scheduler = scheduler

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._sub = ctx.bus.subscribe("kernel/reload", self._on_request, name="reloader.request")

    def _on_request(self, msg) -> None:
        self._queue.append(str((msg.payload or {}).get("module", "")))

    async def teardown(self) -> None:
        self.ctx.bus.unsubscribe(self._sub)

    async def reload(self, name: str) -> tuple[bool, str]:
        sched = self._scheduler
        try:
            old = sched.get(name)
        except KeyError:
            return False, f"no module {name}"
        moving = abs((self.ctx.bus.latest_payload("motion/cmd_applied") or {}).get("linear", 0.0)) > 1e-6
        if old.priority < 10 and moving:
            return False, f"{name} is safety-critical; stop the robot first"
        modname, clsname = type(old).__module__, type(old).__qualname__.split(".")[0]
        try:
            src_mod = sys.modules[modname]
            pyc = importlib.util.cache_from_source(src_mod.__file__) if getattr(src_mod, "__file__", None) else None
            if pyc and os.path.exists(pyc):
                os.remove(pyc)                      # a same-second edit would otherwise hit the stale bytecode
            importlib.invalidate_caches()
            module = importlib.reload(src_mod)
            cls = getattr(module, clsname)
        except Exception as exc:  # noqa: BLE001
            return False, f"import failed: {exc!r}"
        try:
            await old.teardown()
        except Exception:  # noqa: BLE001
            self.log.exception("teardown during reload failed for %s", name)
        new = cls() if getattr(old, "_reload_args", None) is None else cls(*old._reload_args)
        new.rate_hz, new.priority = old.rate_hz, old.priority
        idx = sched.modules.index(old)
        sched.modules[idx] = new
        old.state = ModuleState.STOPPED
        await sched.setup_module(new)
        self.reloads += 1
        self.ctx.safety.audit.record(self.ctx.now, "reloader", "reload", module=name, ok=new.state == ModuleState.READY)
        await self.ctx.bus.publish("kernel/reloaded", {"module": name, "ok": new.state == ModuleState.READY},
                                   source=self.name)
        return new.state == ModuleState.READY, "reloaded"

    async def tick(self, dt: float) -> None:
        while self._queue:
            name = self._queue.pop(0)
            ok, msg = await self.reload(name)
            if not ok:
                await self.ctx.bus.publish("kernel/reload_failed", {"module": name, "reason": msg}, source=self.name)
