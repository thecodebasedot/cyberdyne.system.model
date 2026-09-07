"""Attention: what deserves the brain's focus right now.

Every notable bus event gets a salience = importance x recency decay x
novelty. The top few are published on ``brain/attention``; the single most
salient *actionable* item (an alert or anomaly with a location) is exposed
as ``focus`` so the behaviour tree can act on it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..kernel.context import Context
from ..kernel.module import Module

IMPORTANCE = {
    "security/alert": 1.0, "world/anomaly": 0.7, "brain/question": 0.8, "kernel/module_fault": 0.6,
    "safety/estop_state": 0.9, "human/answer": 0.5, "language/utterance": 0.4, "social/recognised": 0.2,
    "task/failed": 0.6, "fleet/*/security/alert": 0.8, "ops/rolled_back": 0.5,
}


@dataclass
class Item:
    topic: str
    payload: dict
    ts: float
    importance: float
    count: int = 1

    def salience(self, now: float, half_life: float) -> float:
        novelty = 1.0 / math.sqrt(self.count)
        return self.importance * novelty * math.exp(-(now - self.ts) * math.log(2) / half_life)


class AttentionModule(Module):
    name = "attention"
    rate_hz = 2.0
    priority = 49                     # right before the brain

    def __init__(self, half_life: float = 20.0, threshold: float = 0.25) -> None:
        super().__init__()
        self.half_life, self.threshold = half_life, threshold
        self.items: dict[str, Item] = {}
        self.focus: dict | None = None
        self.handled: set[str] = set()

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._subs = [ctx.bus.subscribe(t, self._on_event, name=f"attention.{t}") for t in IMPORTANCE]
        ctx.extras["attention"] = self

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    def _on_event(self, msg) -> None:
        if msg.topic == "safety/estop_state" and not msg.payload.get("engaged"):
            return
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        key = f"{msg.topic}:{payload.get('id', payload.get('track_id', ''))}" if payload else msg.topic
        alert = msg.topic.endswith("/security/alert")
        imp = next((v for k, v in IMPORTANCE.items()
                    if k == msg.topic or (alert and k.endswith("/security/alert"))), 0.3)
        if key in self.items:
            it = self.items[key]
            it.ts, it.count, it.payload = msg.ts, it.count + 1, msg.payload if isinstance(msg.payload, dict) else {}
        else:
            self.items[key] = Item(msg.topic, msg.payload if isinstance(msg.payload, dict) else {}, msg.ts, imp)

    def mark_handled(self, key: str) -> None:
        self.handled.add(key)

    async def tick(self, dt: float) -> None:
        now = self.ctx.now
        for k in [k for k, it in self.items.items() if it.salience(now, self.half_life) < 0.02]:
            del self.items[k]
        ranked = sorted(((it.salience(now, self.half_life), k, it) for k, it in self.items.items()), reverse=True)
        top = [{"key": k, "topic": it.topic, "salience": round(s, 3), "ts": it.ts} for s, k, it in ranked[:5]]
        self.focus = None
        for s, k, it in ranked:
            if s < self.threshold or k in self.handled:
                continue
            if it.topic in ("security/alert", "world/anomaly") and ("x" in it.payload or "id" in it.payload):
                self.focus = {"key": k, "topic": it.topic, "salience": round(s, 3), **it.payload}
                break
        await self.ctx.bus.publish("brain/attention", {"top": top, "focus": self.focus}, source=self.name)
