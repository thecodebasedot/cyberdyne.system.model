"""SkillRunner: executes ``skill/invoke`` requests under the permission policy.

    in : skill/invoke {skill, args, confirmed?, request_id?}
    out: skill/result {skill, ok, output, error, request_id}
"""
from __future__ import annotations

import asyncio

from ..kernel.context import Context
from ..kernel.module import Module
from ..safety.permissions import PermissionDenied, Tier
from .base import SkillResult
from .registry import SkillRegistry


class SkillRunner(Module):
    name = "skills"
    rate_hz = 10.0
    priority = 55

    def __init__(self, registry: SkillRegistry | None = None, timeout: float = 5.0) -> None:
        super().__init__()
        self.registry = registry or SkillRegistry()
        self.timeout = timeout
        self._queue: list[dict] = []
        self.invocations = 0

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        if not self.registry.names():
            self.registry.load_builtin()
        for dotted in ctx.config.skills:
            self.registry.load_module(dotted)
        self._sub = ctx.bus.subscribe("skill/invoke", self._on_invoke, name="skills.invoke")
        ctx.extras["skills"] = self.registry

    async def teardown(self) -> None:
        self.ctx.bus.unsubscribe(self._sub)

    def _on_invoke(self, msg) -> None:
        self._queue.append(dict(msg.payload))

    async def invoke(self, name: str, args: dict | None = None, confirmed: bool = False) -> SkillResult:
        args = args or {}
        try:
            skill = self.registry.get(name)
            tier = self.ctx.safety.permissions.check(skill.manifest.permission, confirmed=confirmed)
        except (KeyError, PermissionDenied) as exc:
            self.ctx.safety.audit.record(self.ctx.now, "skills", "denied", skill=name, error=str(exc))
            return SkillResult(False, error=str(exc))
        if tier in (Tier.LOG, Tier.CONFIRM):
            self.ctx.safety.audit.record(self.ctx.now, "skills", "invoke", skill=name, args=args, tier=tier.value)
        try:
            result = await asyncio.wait_for(skill.run(self.ctx, args), self.timeout)
        except TimeoutError:
            result = SkillResult(False, error=f"{name} timed out after {self.timeout}s")
        except Exception as exc:  # noqa: BLE001
            result = SkillResult(False, error=repr(exc))
        self.invocations += 1
        return result

    async def tick(self, dt: float) -> None:
        while self._queue:
            req = self._queue.pop(0)
            res = await self.invoke(req.get("skill", ""), req.get("args"), bool(req.get("confirmed")))
            await self.ctx.bus.publish("skill/result", {"skill": req.get("skill"),
                                                        "request_id": req.get("request_id"),
                                                        **res.to_dict()}, source=self.name)
