"""Tasks: long-horizon work made of steps, with pause/resume and retries.

A step is one of
    goto   {x, y, name}            -> nav/goal, done on nav/arrived
    skill  {skill, args}           -> skill/invoke, done on matching skill/result
    wait   {seconds}               -> done when the kernel clock says so
    task   {name}                  -> a named sub-task from the library (composition)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskStep:
    kind: str                       # goto | skill | wait | task
    args: dict[str, Any] = field(default_factory=dict)
    timeout: float = 120.0
    retries: int = 1
    note: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> TaskStep:
        d = dict(d)
        if "kind" not in d:                       # plan-step shape {skill, args}
            skill = d.pop("skill")
            if skill == "goto":
                return cls("goto", dict(d.get("args") or {}), note=d.get("note", ""))
            return cls("skill", {"skill": skill, "args": dict(d.get("args") or {})}, note=d.get("note", ""))
        return cls(d["kind"], dict(d.get("args") or {}), float(d.get("timeout", 120.0)),
                   int(d.get("retries", 1)), str(d.get("note", "")))

    def to_dict(self) -> dict:
        return {"kind": self.kind, "args": self.args, "timeout": self.timeout, "retries": self.retries,
                "note": self.note}


@dataclass
class Task:
    name: str
    steps: list[TaskStep]
    id: int = 0                     # assigned by the TaskRunner (per-runtime, deterministic)
    status: TaskStatus = TaskStatus.PENDING
    idx: int = 0
    attempts: int = 0
    origin: str = ""
    trust: str = "owner"
    started: float = 0.0
    finished: float = 0.0
    step_started: float = 0.0
    in_flight: bool = False
    results: list[dict] = field(default_factory=list)
    error: str = ""
    parent: int | None = None

    @property
    def current(self) -> TaskStep | None:
        return self.steps[self.idx] if self.idx < len(self.steps) else None

    @property
    def active(self) -> bool:
        return self.status in (TaskStatus.RUNNING, TaskStatus.PAUSED)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "status": self.status.value, "step": self.idx,
                "steps": len(self.steps), "current": self.current.to_dict() if self.current else None,
                "attempts": self.attempts, "origin": self.origin, "trust": self.trust, "error": self.error,
                "started": self.started, "finished": self.finished, "parent": self.parent}
