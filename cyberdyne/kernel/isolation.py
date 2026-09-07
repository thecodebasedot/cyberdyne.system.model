"""Process isolation for pure modules.

A ``PureModule`` is a function of its inputs: ``compute(dt, inputs) ->
outputs`` where inputs are the latest payloads of the topics it declares
and outputs are ``(topic, payload)`` pairs. ``IsolatedModule`` hosts one in
a child process and drives it in lock-step (send inputs, wait for
outputs), so a crash, hang or memory blow-up in the child becomes an
ordinary module fault in the parent; the watchdog then restarts it, which
respawns the process. Determinism is preserved because the parent waits.
"""
from __future__ import annotations

import multiprocessing as mp
import traceback
from abc import ABC, abstractmethod
from typing import Any

from .context import Context
from .module import Module


class PureModule(ABC):
    inputs: tuple[str, ...] = ()

    @abstractmethod
    def compute(self, dt: float, inputs: dict[str, Any]) -> list[tuple[str, Any]]: ...


def _child_main(factory, conn) -> None:  # pragma: no cover - runs in the child
    try:
        module = factory()
        conn.send(("ready", None))
        while True:
            cmd, arg = conn.recv()
            if cmd == "stop":
                break
            dt, inputs = arg
            try:
                conn.send(("ok", module.compute(dt, inputs)))
            except Exception:  # noqa: BLE001
                conn.send(("error", traceback.format_exc()))
    except Exception:  # noqa: BLE001
        try:
            conn.send(("error", traceback.format_exc()))
        except Exception:  # noqa: BLE001
            pass


class IsolatedModule(Module):
    max_faults = 1                      # one bad tick = restart the process

    def __init__(self, factory, name: str, rate_hz: float = 10.0, priority: int = 50,
                 timeout: float = 2.0) -> None:
        super().__init__()
        self.factory = factory
        self.name = name
        self.rate_hz = rate_hz
        self.priority = priority
        self.timeout = timeout
        self._proc: mp.Process | None = None
        self._conn = None
        self.spawns = 0
        self.inputs: tuple[str, ...] = getattr(factory, "inputs", ()) or ()

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._spawn()

    def _spawn(self) -> None:
        self._kill()
        parent, child = mp.Pipe()
        self._proc = mp.get_context("fork").Process(target=_child_main, args=(self.factory, child), daemon=True)
        self._proc.start()
        child.close()
        self._conn = parent
        self.spawns += 1
        if not parent.poll(self.timeout * 5):
            raise RuntimeError(f"{self.name}: child did not start")
        status, detail = parent.recv()
        if status != "ready":
            raise RuntimeError(f"{self.name}: child failed to start: {detail}")

    def _kill(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.kill()
            self._proc.join(1.0)
        self._proc = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def teardown(self) -> None:
        if self._conn is not None:
            try:
                self._conn.send(("stop", None))
            except Exception:  # noqa: BLE001
                pass
        self._kill()

    async def tick(self, dt: float) -> None:
        if self._conn is None or self._proc is None or not self._proc.is_alive():
            raise RuntimeError(f"{self.name}: child process is dead")
        inputs = {t: self.ctx.bus.latest_payload(t) for t in self.inputs}
        self._conn.send(("tick", (dt, inputs)))
        if not self._conn.poll(self.timeout):
            self._kill()
            raise TimeoutError(f"{self.name}: child hung for {self.timeout}s")
        status, result = self._conn.recv()
        if status != "ok":
            raise RuntimeError(f"{self.name}: child error: {result.strip().splitlines()[-1]}")
        for topic, payload in result:
            await self.ctx.bus.publish(topic, payload, source=self.name)
