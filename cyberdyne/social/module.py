"""SocialModule: turns tracks into social behaviour.

    in : perception/tracks
    out: social/recognised {track_id, name, trust}
         speech/say        {text, voice}          greeting (rate-limited per person)
         security/alert    {track_id, x, y}       unknown person while armed
         world/observe     {id, kind, x, y, attrs} for the entity store
"""
from __future__ import annotations

from ..kernel.context import Context
from ..kernel.module import Module
from .identity import IdentityRegistry, Trust


class SocialModule(Module):
    name = "social"
    rate_hz = 5.0
    priority = 35

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        cfg = ctx.config.social
        self.identity = IdentityRegistry(cfg.known)
        self.greeted: dict[str, float] = {}
        self.alerted: set[int] = set()
        self.armed = cfg.armed
        ctx.extras["identity"] = self.identity
        self._sub = ctx.bus.subscribe("security/arm", self._on_arm, name="social.arm")

    async def teardown(self) -> None:
        self.ctx.bus.unsubscribe(self._sub)

    def _on_arm(self, msg) -> None:
        self.armed = bool((msg.payload or {}).get("armed", True))

    async def tick(self, dt: float) -> None:
        bus, cfg, now = self.ctx.bus, self.ctx.config.social, self.ctx.now
        for t in bus.latest_payload("perception/tracks", []) or []:
            person = self.identity.identify(t.get("signature", ""))
            name = person.name if person else None
            trust = person.trust if person else Trust.UNKNOWN
            entity_id = f"{t['kind']}:{name}" if name else f"{t['kind']}#{t['track_id']}"
            await bus.publish("world/observe", {"id": entity_id, "kind": t["kind"], "x": t["x"], "y": t["y"],
                                                "attrs": {"trust": trust.value, "track_id": t["track_id"]}},
                              source=self.name)
            if t["kind"] != "person":
                continue
            if person:
                await bus.publish("social/recognised", {"track_id": t["track_id"], "name": name,
                                                        "trust": trust.value}, source=self.name)
                if now - self.greeted.get(name, -1e9) > cfg.greet_cooldown:
                    self.greeted[name] = now
                    await bus.publish("speech/say", {"text": f"Hello {name}.", "voice": "friendly"},
                                      source=self.name)
            elif self.armed and t["track_id"] not in self.alerted and t.get("signature"):
                # a signature we received but cannot match = a stranger; no signature = too far to tell
                self.alerted.add(t["track_id"])
                self.ctx.safety.audit.record(now, "social", "security.alert", track=t["track_id"],
                                             x=t["x"], y=t["y"])
                await bus.publish("security/alert", {"track_id": t["track_id"], "x": t["x"], "y": t["y"],
                                                     "reason": "unrecognised person while armed"},
                                  source=self.name)
                await bus.publish("speech/say", {"text": "Unrecognised person detected. Please identify yourself.",
                                                 "voice": "alert"}, source=self.name)
