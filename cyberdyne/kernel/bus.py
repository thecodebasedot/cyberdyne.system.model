"""Typed publish/subscribe message bus.

Topics are slash-separated strings (``motion/cmd``, ``kernel/fault``) and
subscriptions accept ``fnmatch`` wildcards (``perception/*``, ``*``).
Handlers may be sync or async. A failing handler never takes down the
publisher: the exception is captured and re-published on ``kernel/fault``.
"""
from __future__ import annotations

import asyncio
import fnmatch
import inspect
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .clock import Clock

log = logging.getLogger("cyberdyne.bus")

Handler = Callable[["Message"], None | Awaitable[None]]


@dataclass(frozen=True)
class Message:
    topic: str
    payload: Any
    source: str
    ts: float
    seq: int

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "payload": self.payload, "source": self.source,
                "ts": self.ts, "seq": self.seq}


@dataclass
class _Subscription:
    pattern: str
    handler: Handler
    name: str = ""


@dataclass
class BusStats:
    published: int = 0
    delivered: int = 0
    handler_errors: int = 0
    per_topic: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class MessageBus:
    FAULT_TOPIC = "kernel/fault"

    def __init__(self, clock: Clock, history: int = 2000) -> None:
        self._clock = clock
        self._subs: list[_Subscription] = []
        self._seq = 0
        self._latest: dict[str, Message] = {}
        self._history: deque[Message] = deque(maxlen=history)
        self.stats = BusStats()

    # -- subscription -----------------------------------------------------
    def subscribe(self, pattern: str, handler: Handler, name: str = "") -> _Subscription:
        sub = _Subscription(pattern, handler, name or getattr(handler, "__qualname__", "?"))
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: _Subscription) -> None:
        self._subs = [s for s in self._subs if s is not sub]

    # -- publishing -------------------------------------------------------
    async def publish(self, topic: str, payload: Any = None, source: str = "kernel") -> Message:
        self._seq += 1
        msg = Message(topic, payload, source, self._clock.now(), self._seq)
        self._latest[topic] = msg
        self._history.append(msg)
        self.stats.published += 1
        self.stats.per_topic[topic] += 1

        for sub in list(self._subs):
            if not fnmatch.fnmatchcase(topic, sub.pattern):
                continue
            try:
                result = sub.handler(msg)
                if inspect.isawaitable(result):
                    await result
                self.stats.delivered += 1
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                self.stats.handler_errors += 1
                log.exception("handler %s failed on %s", sub.name, topic)
                if topic != self.FAULT_TOPIC:
                    await self.publish(self.FAULT_TOPIC,
                                       {"kind": "handler", "handler": sub.name,
                                        "topic": topic, "error": repr(exc)},
                                       source="bus")
        return msg

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, topic: str,
                           payload: Any = None, source: str = "external") -> None:
        """Entry point for non-async threads (HTTP server, hardware ISRs)."""
        asyncio.run_coroutine_threadsafe(self.publish(topic, payload, source), loop)

    # -- introspection ----------------------------------------------------
    def latest(self, topic: str) -> Message | None:
        return self._latest.get(topic)

    def latest_payload(self, topic: str, default: Any = None) -> Any:
        msg = self._latest.get(topic)
        return msg.payload if msg else default

    def snapshot(self) -> dict[str, Any]:
        return {t: m.to_dict() for t, m in self._latest.items()}

    def history(self, pattern: str = "*", limit: int = 100) -> list[Message]:
        out = [m for m in self._history if fnmatch.fnmatchcase(m.topic, pattern)]
        return out[-limit:]

    def topics(self) -> list[str]:
        return sorted(self._latest)
