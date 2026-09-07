"""Skill = manifest + async ``run``.

The manifest declares the permission action the skill needs, so the runner
can consult the safety policy *before* the skill's code ever executes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..kernel.context import Context


@dataclass(frozen=True)
class SkillManifest:
    name: str
    version: str = "0.1.0"
    description: str = ""
    action: str = ""                      # permission action, defaults to skill.<name>
    args: dict[str, str] = field(default_factory=dict)   # arg -> description
    tags: tuple[str, ...] = ()

    @property
    def permission(self) -> str:
        return self.action or f"skill.{self.name}"

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "description": self.description,
                "permission": self.permission, "args": dict(self.args), "tags": list(self.tags)}


@dataclass
class SkillResult:
    ok: bool
    output: Any = None
    error: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "output": self.output, "error": self.error}


class Skill(ABC):
    manifest: SkillManifest

    @abstractmethod
    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult: ...
