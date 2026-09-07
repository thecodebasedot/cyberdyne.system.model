"""LLM backends.

``LLMBackend`` is the single seam between the robot and any language model.
Everything above it (planner, interpreter, council roles) only ever calls
``complete(system, prompt)`` and parses text, so a backend can be the
Anthropic API, a local model, or a scripted stub for tests.

The Anthropic SDK is an optional dependency (``pip install -e ".[llm]"``);
it is imported lazily so the robot runs without it.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("cyberdyne.llm")

DEFAULT_MODEL = "claude-opus-5"


class LLMError(RuntimeError):
    """Transport or API failure. Callers fall back to rule-based behaviour."""


class LLMRefused(LLMError):
    def __init__(self, category: str | None, explanation: str | None) -> None:
        super().__init__(f"model refused ({category}): {explanation}")
        self.category, self.explanation = category, explanation


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None

    def json(self) -> dict:
        """Extract the first JSON object in the text (models often wrap it in prose/fences)."""
        return extract_json(self.text)


def extract_json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [m.group(1)] if m else []
    start = text.find("{")
    if start >= 0:
        candidates.append(text[start:text.rfind("}") + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no JSON object in model output: {text[:120]!r}")


class LLMBackend(ABC):
    name: str = "llm"

    @abstractmethod
    async def complete(self, system: str, prompt: str, *, effort: str | None = None,
                       max_tokens: int | None = None) -> LLMResponse: ...

    def describe(self) -> dict:
        return {"backend": self.name}


class ScriptedBackend(LLMBackend):
    """Deterministic stand-in: a list of canned replies or a callable(system, prompt) -> str."""
    name = "scripted"

    def __init__(self, replies: list[str] | Callable[[str, str], str] | None = None) -> None:
        self._replies = replies if callable(replies) else list(replies or [])
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, prompt: str, *, effort: str | None = None,
                       max_tokens: int | None = None) -> LLMResponse:
        self.calls.append((system, prompt))
        if callable(self._replies):
            text = self._replies(system, prompt)
        elif self._replies:
            text = self._replies.pop(0)
        else:
            raise LLMError("scripted backend has no reply left")
        return LLMResponse(text, model=self.name)


@dataclass
class AnthropicBackend(LLMBackend):
    """Claude via the official ``anthropic`` SDK (async client).

    Credentials resolve from the environment (``ANTHROPIC_API_KEY`` or an
    ``ant auth login`` profile); nothing is hard-coded here.
    """
    model: str = DEFAULT_MODEL
    effort: str = "high"
    max_tokens: int = 8000
    timeout: float = 90.0
    max_retries: int = 2
    stats: dict[str, int] = field(default_factory=lambda: {"calls": 0, "errors": 0, "refusals": 0,
                                                           "input_tokens": 0, "output_tokens": 0})
    name = "anthropic"

    def __post_init__(self) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise LLMError("anthropic SDK not installed: pip install -e '.[llm]'") from exc
        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(timeout=self.timeout, max_retries=self.max_retries)

    async def complete(self, system: str, prompt: str, *, effort: str | None = None,
                       max_tokens: int | None = None) -> LLMResponse:
        a = self._anthropic
        self.stats["calls"] += 1
        try:
            resp = await self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens or self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": effort or self.effort},
            )
        except a.RateLimitError as exc:
            self.stats["errors"] += 1
            raise LLMError(f"rate limited: {exc.message}") from exc
        except a.APIStatusError as exc:
            self.stats["errors"] += 1
            raise LLMError(f"api error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            self.stats["errors"] += 1
            raise LLMError(f"connection error: {exc}") from exc
        if resp.stop_reason == "refusal":
            self.stats["refusals"] += 1
            det = resp.stop_details
            raise LLMRefused(getattr(det, "category", None), getattr(det, "explanation", None))
        text = "".join(b.text for b in resp.content if b.type == "text")
        self.stats["input_tokens"] += resp.usage.input_tokens
        self.stats["output_tokens"] += resp.usage.output_tokens
        return LLMResponse(text, resp.model, resp.usage.input_tokens, resp.usage.output_tokens, resp)

    def describe(self) -> dict:
        return {"backend": self.name, "model": self.model, "effort": self.effort, **self.stats}


def build_backend(kind: str, model: str = DEFAULT_MODEL, effort: str = "high") -> LLMBackend | None:
    """``none`` -> no LLM (rule-based robot); ``anthropic`` -> Claude; ``scripted`` -> empty stub."""
    if kind in ("", "none", "off"):
        return None
    if kind == "anthropic":
        return AnthropicBackend(model=model, effort=effort)
    if kind == "scripted":
        return ScriptedBackend([])
    raise ValueError(f"unknown llm backend: {kind}")
