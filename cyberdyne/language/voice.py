"""VoiceModule: the ears and the mouth.

    mic  -> language/utterance {text, speaker, trust, signature, addressed}
    speech/say, brain/question, skill/result (opt) -> speaker -> speech/said

Wake-word gating: with ``require_wake_word`` on, speech that does not start
with a wake word is ignored (still logged on ``speech/overheard``).
"""
from __future__ import annotations

import re

from ..hal.interfaces import DeviceKind, Microphone, Speaker
from ..kernel.context import Context
from ..kernel.module import Module
from .affect import mood


class VoiceModule(Module):
    name = "voice"
    rate_hz = 10.0
    priority = 48

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.mic = ctx.devices.get(DeviceKind.MIC, Microphone) if ctx.devices.has(DeviceKind.MIC) else None
        self.speaker = ctx.devices.get(DeviceKind.SPEAKER, Speaker) if ctx.devices.has(DeviceKind.SPEAKER) else None
        cfg = ctx.config.social
        self._wake = re.compile(r"^(?:" + "|".join(re.escape(w) for w in sorted(cfg.wake_words, key=len,
                                                                                    reverse=True)) + r")[,!:]?\s*",
                                re.I)
        self._queue: list[tuple[str, str]] = []
        self.heard = self.ignored = self.said = 0
        self._subs = [ctx.bus.subscribe("speech/say", self._on_say, name="voice.say"),
                      ctx.bus.subscribe("brain/question", self._on_question, name="voice.question")]
        if cfg.speak_results:
            self._subs.append(ctx.bus.subscribe("skill/result", self._on_result, name="voice.result"))

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    def _on_say(self, msg) -> None:
        p = msg.payload or {}
        self._queue.append((str(p.get("text", "")), str(p.get("voice", "neutral"))))

    def _on_question(self, msg) -> None:
        self._queue.append((str(msg.payload["question"]), "neutral"))

    def _on_result(self, msg) -> None:
        p = msg.payload or {}
        spoken = ("echo", "status", "time", "find", "explain", "describe", "device", "remind")
        if p.get("skill") in spoken and p.get("ok"):
            out = p.get("output")
            text = out if isinstance(out, str) else (out.get("speech") if isinstance(out, dict) else None)
            if text:
                self._queue.append((str(text), "neutral"))
        elif p.get("ok") is False and p.get("error"):
            self._queue.append((f"Sorry, I can't: {p['error']}", "neutral"))

    async def tick(self, dt: float) -> None:
        bus, cfg = self.ctx.bus, self.ctx.config.social
        identity = self.ctx.extras.get("identity")
        if self.mic:
            for u in await self.mic.listen():
                m = self._wake.match(u.text)
                addressed = bool(m)
                text = u.text[m.end():] if m else u.text
                person = identity.identify(u.signature) if identity else None
                trust = (person.trust.value if person else "unknown")
                if cfg.require_wake_word and not addressed:
                    self.ignored += 1
                    await bus.publish("speech/overheard", {"text": u.text, "speaker": person.name if person else None},
                                      source=self.name)
                    continue
                self.heard += 1
                score, words = mood(u.text)
                if words:
                    await bus.publish("language/affect", {"speaker": person.name if person else None,
                                                          "mood": round(score, 2), "words": words}, source=self.name)
                if u.bearing is not None:
                    await bus.publish("speech/heard", {"bearing": round(u.bearing, 3),
                                                       "speaker": person.name if person else None}, source=self.name)
                await bus.publish("language/utterance",
                                  {"text": text.strip(), "speaker": person.name if person else None,
                                   "trust": trust, "signature": u.signature, "addressed": addressed,
                                   "confirmed": False}, source=self.name)
        while self._queue:
            text, voice = self._queue.pop(0)
            if self.speaker:
                await self.speaker.say(text, voice)
            self.said += 1
            await bus.publish("speech/said", {"text": text, "voice": voice}, source=self.name)
