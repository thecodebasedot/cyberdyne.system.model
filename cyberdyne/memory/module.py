"""MemoryModule: listens to the bus and turns notable events into episodes."""
from __future__ import annotations

from ..kernel.context import Context
from ..kernel.module import Module
from .episodic import EpisodicMemory
from .semantic import KnowledgeGraph
from .working import WorkingMemory

_WATCH = {
    "kernel/state": (0.7, lambda p: f"state {p['from']} -> {p['to']} ({p.get('reason', '')})"),
    "kernel/fault": (0.8, lambda p: f"fault in {p.get('module') or p.get('handler')}: {p.get('error')}"),
    "kernel/module_restart": (0.6, lambda p: f"restarted {p['module']}"),
    "safety/estop_state": (0.95, lambda p: "e-stop engaged: " + p["reason"] if p["engaged"] else "e-stop reset"),
    "nav/arrived": (0.5, lambda p: f"arrived at {p.get('name') or (p['x'], p['y'])}"),
    "nav/goal": (0.4, lambda p: f"new goal {p.get('name') or (p['x'], p['y'])}"),
    "skill/result": (0.4, lambda p: f"skill {p['skill']} -> {'ok' if p['ok'] else 'failed'}"),
    "language/utterance": (0.6, lambda p: f"user said: {p['text']}"),
    "brain/decision": (0.7, lambda p: f"decided on '{p['goal']}': "
                                      f"{'approved' if p['approved'] else 'held'} ({p['confidence']:.0%})"),
    "brain/question": (0.8, lambda p: f"asked human: {p['question']}"),
    "human/answer": (0.8, lambda p: f"human answered: {p.get('answer')}"),
}


class MemoryModule(Module):
    name = "memory"
    rate_hz = 1.0
    priority = 60

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.working = WorkingMemory(ctx.clock)
        self.episodic = EpisodicMemory()
        self.semantic = KnowledgeGraph()
        self.semantic.add("self", "name", ctx.config.name, "config", ctx.now)
        self._subs = [ctx.bus.subscribe(t, self._on_event, name=f"memory.{t}") for t in _WATCH]
        ctx.extras["memory"] = self

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    def _on_event(self, msg) -> None:
        importance, fmt = _WATCH[msg.topic]
        try:
            summary = fmt(msg.payload)
        except Exception:  # noqa: BLE001
            summary = f"{msg.topic}: {msg.payload!r}"[:120]
        self.episodic.remember(msg.ts, msg.topic, summary, importance, payload=msg.payload)

    def forget(self, about: str) -> dict:
        """Privacy primitive: erase everything mentioning ``about`` from every store."""
        n_ep = self.episodic.forget(about)
        n_kg = self.semantic.forget(about)
        n_wm = 0
        for k, v in list(self.working.to_dict().items()):
            if about.lower() in k.lower() or about.lower() in str(v).lower():
                self.working.pop(k)
                n_wm += 1
        return {"episodes": n_ep, "facts": n_kg, "working": n_wm}

    async def tick(self, dt: float) -> None:
        self.working.sweep()
        await self.ctx.bus.publish("memory/summary", {"episodes": len(self.episodic),
                                                      "facts": len(self.semantic),
                                                      "working": len(self.working.to_dict())},
                                   source=self.name)
