"""UserModel: what each person tends to ask for, and when.

Counts (person, hour-of-day bucket, request) triples. Once a habit has been
seen ``suggest_after`` times, the robot *suggests* it at that hour (speech +
``brain/suggestion``); it never acts on a habit by itself.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from ..kernel.context import Context
from ..kernel.module import Module


@dataclass
class UserModel:
    counts: dict[str, dict[str, dict[str, int]]] = field(default_factory=lambda: defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))))          # person -> hour -> request -> n
    totals: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    mood: dict[str, float] = field(default_factory=dict)          # person -> exponential moving average

    def observe_mood(self, person: str, score: float, alpha: float = 0.3) -> float:
        prev = self.mood.get(person, 0.0)
        self.mood[person] = round(prev + alpha * (score - prev), 3)
        return self.mood[person]

    @staticmethod
    def bucket(hour: int) -> str:
        return {0: "night", 1: "morning", 2: "afternoon", 3: "evening"}[min(3, hour // 6)]

    def observe(self, person: str, request: str, hour: int) -> None:
        self.counts[person][self.bucket(hour)][request] += 1
        self.totals[person] += 1

    def habits(self, person: str, hour: int, min_count: int = 3) -> list[tuple[str, int]]:
        b = self.counts.get(person, {}).get(self.bucket(hour), {})
        return sorted(((r, n) for r, n in b.items() if n >= min_count), key=lambda x: -x[1])

    def preferences(self, person: str) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for bucket in self.counts.get(person, {}).values():
            for r, n in bucket.items():
                out[r] += n
        return dict(sorted(out.items(), key=lambda x: -x[1]))

    def to_dict(self) -> dict:
        return {"counts": {p: {b: dict(r) for b, r in hb.items()} for p, hb in self.counts.items()},
                "totals": dict(self.totals), "mood": dict(self.mood)}

    @classmethod
    def from_dict(cls, d: dict) -> UserModel:
        m = cls()
        for p, hb in (d.get("counts") or {}).items():
            for b, rs in hb.items():
                for r, n in rs.items():
                    m.counts[p][b][r] = int(n)
        for p, n in (d.get("totals") or {}).items():
            m.totals[p] = int(n)
        m.mood = {p: float(v) for p, v in (d.get("mood") or {}).items()}
        return m


class UserModelModule(Module):
    name = "user_model"
    rate_hz = 0.5
    priority = 65

    def __init__(self, model: UserModel | None = None, hour_fn=None) -> None:
        super().__init__()
        self.model = model or UserModel()
        self.hour_fn = hour_fn or (lambda: time.localtime().tm_hour)
        self._last_suggest: dict[str, float] = {}
        self.suggestions = 0

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._sub = ctx.bus.subscribe("language/intent", self._on_intent, name="user_model.intent")
        self._sub2 = ctx.bus.subscribe("language/utterance", self._on_utterance, name="user_model.utt")
        self._sub3 = ctx.bus.subscribe("language/affect", self._on_affect, name="user_model.affect")
        self._speaker: str | None = None
        ctx.extras["user_model"] = self

    async def teardown(self) -> None:
        self.ctx.bus.unsubscribe(self._sub)
        self.ctx.bus.unsubscribe(self._sub2)
        self.ctx.bus.unsubscribe(self._sub3)

    def _on_affect(self, msg) -> None:
        p = msg.payload
        if p.get("speaker"):
            self.model.observe_mood(p["speaker"], float(p["mood"]))

    def _on_utterance(self, msg) -> None:
        self._speaker = (msg.payload or {}).get("speaker")

    def _on_intent(self, msg) -> None:
        p = msg.payload or {}
        if not self._speaker or p.get("skill") in ("answer", "explain", "status", "time", "echo"):
            return
        request = p["skill"] + ("" if not p.get("args") else ":" + ",".join(f"{k}={v}" for k, v in
                                                                             sorted(p["args"].items())))
        self.model.observe(self._speaker, request, self.hour_fn())

    async def tick(self, dt: float) -> None:
        cfg = self.ctx.config.learning
        if not cfg.user_model:
            return
        hour = self.hour_fn()
        present = {t["name"] for t in self.ctx.bus.history("social/recognised", 20)
                   if self.ctx.now - t["ts"] < 10} if False else set()
        for m in self.ctx.bus.history("social/recognised", 30):
            if self.ctx.now - m.ts < 10:
                present.add(m.payload["name"])
        for person in present:
            if self.ctx.now - self._last_suggest.get(person, -1e9) < cfg.suggest_cooldown:
                continue
            habits = self.model.habits(person, hour, cfg.suggest_after)
            if habits:
                request, n = habits[0]
                self._last_suggest[person] = self.ctx.now
                self.suggestions += 1
                await self.ctx.bus.publish("brain/suggestion", {"person": person, "request": request, "seen": n,
                                                                "bucket": UserModel.bucket(hour)}, source=self.name)
                await self.ctx.bus.publish("speech/say", {"text": f"{person}, you usually ask for {request} "
                                                                  f"around now. Shall I?", "voice": "friendly"},
                                           source=self.name)
