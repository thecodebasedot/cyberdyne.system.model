"""LanguageModule: user text -> intent -> skill invocation.

    in : language/utterance {text, speaker?}
    out: language/intent {skill,args,...} | language/unknown {text}
         skill/invoke
"""
from __future__ import annotations

from ..kernel.context import Context
from ..kernel.module import Module
from .interpreter import Interpreter, RuleInterpreter


class LanguageModule(Module):
    name = "language"
    rate_hz = 5.0
    priority = 52

    def __init__(self, interpreter: Interpreter | None = None) -> None:
        super().__init__()
        self.interpreter = interpreter or RuleInterpreter()
        self._pending: list[dict] = []
        self.understood = 0
        self.unknown = 0

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self._sub = ctx.bus.subscribe("language/utterance", self._on_utterance, name="language.utterance")

    async def teardown(self) -> None:
        self.ctx.bus.unsubscribe(self._sub)

    def _on_utterance(self, msg) -> None:
        p = msg.payload if isinstance(msg.payload, dict) else {"text": str(msg.payload)}
        self._pending.append(p)

    async def tick(self, dt: float) -> None:
        bus = self.ctx.bus
        while self._pending:
            utt = self._pending.pop(0)
            text = str(utt.get("text", ""))
            intent = await self.interpreter.parse_async(text)
            if intent is None:
                self.unknown += 1
                await bus.publish("language/unknown", {"text": text}, source=self.name)
                continue
            self.understood += 1
            args = dict(intent.args)
            if intent.skill == "goto" and args.pop("charger", False):
                args.update(self.ctx.config.world.charger, name="charger")
            await bus.publish("language/intent", intent.to_dict(), source=self.name)
            await bus.publish("skill/invoke", {"skill": intent.skill, "args": args,
                                               "confirmed": bool(utt.get("confirmed")),
                                               "request_id": utt.get("request_id")}, source=self.name)
