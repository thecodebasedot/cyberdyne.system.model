"""Behaviour tree primitives with async leaves."""
from __future__ import annotations

import inspect
from collections.abc import Callable
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"


class Node:
    label: str = "node"

    async def tick(self, bb: dict[str, Any]) -> Status:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"type": type(self).__name__, "label": self.label}


async def _call(fn: Callable, bb: dict) -> Any:
    r = fn(bb)
    return await r if inspect.isawaitable(r) else r


class Action(Node):
    def __init__(self, label: str, fn: Callable[[dict], Any]) -> None:
        self.label, self.fn = label, fn

    async def tick(self, bb: dict) -> Status:
        r = await _call(self.fn, bb)
        if isinstance(r, Status):
            return r
        return Status.SUCCESS if r or r is None else Status.FAILURE


class Condition(Node):
    def __init__(self, label: str, fn: Callable[[dict], Any]) -> None:
        self.label, self.fn = label, fn

    async def tick(self, bb: dict) -> Status:
        return Status.SUCCESS if await _call(self.fn, bb) else Status.FAILURE


class Inverter(Node):
    def __init__(self, child: Node) -> None:
        self.child = child
        self.label = f"not {child.label}"

    async def tick(self, bb: dict) -> Status:
        s = await self.child.tick(bb)
        return {Status.SUCCESS: Status.FAILURE, Status.FAILURE: Status.SUCCESS}.get(s, s)

    def describe(self) -> dict:
        return {**super().describe(), "children": [self.child.describe()]}


class _Composite(Node):
    def __init__(self, label: str, *children: Node) -> None:
        self.label, self.children = label, list(children)
        self.last_active: str | None = None

    def describe(self) -> dict:
        return {**super().describe(), "active": self.last_active,
                "children": [c.describe() for c in self.children]}


class Sequence(_Composite):
    """Runs children in order; fails on first failure."""

    async def tick(self, bb: dict) -> Status:
        for c in self.children:
            self.last_active = c.label
            s = await c.tick(bb)
            if s != Status.SUCCESS:
                return s
        return Status.SUCCESS


class Selector(_Composite):
    """Runs children in order; succeeds on first success."""

    async def tick(self, bb: dict) -> Status:
        for c in self.children:
            self.last_active = c.label
            s = await c.tick(bb)
            if s != Status.FAILURE:
                return s
        return Status.FAILURE
