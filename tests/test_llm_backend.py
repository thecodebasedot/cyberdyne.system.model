"""AnthropicBackend against the real SDK classes (network mocked), plus an opt-in live test."""
import json
import os
from types import SimpleNamespace

import pytest

anthropic = pytest.importorskip("anthropic")

from cyberdyne.cognition.eval_llm import run_eval  # noqa: E402
from cyberdyne.cognition.llm import AnthropicBackend, LLMError, LLMRefused, ScriptedBackend, build_backend  # noqa: E402
from tests.conftest import run  # noqa: E402


def _message(text: str, stop_reason: str = "end_turn", **extra):
    usage = SimpleNamespace(input_tokens=12, output_tokens=7)
    blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, stop_details=extra.get("stop_details"),
                           model="claude-opus-5", usage=usage)


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    b = build_backend("anthropic", effort="low")
    assert isinstance(b, AnthropicBackend) and b.model == "claude-opus-5"
    return b


def test_backend_request_shape_and_parsing(backend, monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _message('Here you go:\n```json\n{"steps": [{"skill": "goto", "args": {"x": 1, "y": 2}}]}\n```')
    monkeypatch.setattr(backend._client.messages, "create", fake_create)
    resp = run(backend.complete("SYS", "PROMPT", effort="high"))
    assert resp.json()["steps"][0]["skill"] == "goto" and resp.input_tokens == 12
    assert captured["model"] == "claude-opus-5" and captured["output_config"] == {"effort": "high"}
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"} and captured["system"][0]["text"] == "SYS"
    assert captured["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert "thinking" not in captured                       # Opus 5: adaptive thinking is the default
    assert backend.describe()["calls"] == 1 and backend.describe()["output_tokens"] == 7


def test_backend_maps_sdk_errors(backend, monkeypatch):
    req = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    resp429 = SimpleNamespace(status_code=429, headers={"retry-after": "1"}, request=req)

    async def rate_limited(**kwargs):
        raise anthropic.RateLimitError("slow down", response=resp429, body=None)
    monkeypatch.setattr(backend._client.messages, "create", rate_limited)
    with pytest.raises(LLMError, match="rate limited"):
        run(backend.complete("s", "p"))

    async def down(**kwargs):
        raise anthropic.APIConnectionError(request=req)
    monkeypatch.setattr(backend._client.messages, "create", down)
    with pytest.raises(LLMError, match="connection"):
        run(backend.complete("s", "p"))

    async def refused(**kwargs):
        return _message("", "refusal", stop_details=SimpleNamespace(category="cyber", explanation="no"))
    monkeypatch.setattr(backend._client.messages, "create", refused)
    with pytest.raises(LLMRefused) as exc:
        run(backend.complete("s", "p"))
    assert exc.value.category == "cyber"
    assert backend.describe()["errors"] == 2 and backend.describe()["refusals"] == 1


def test_eval_harness_runs_on_scripted_backend():
    replies = [json.dumps({"skill": "plan", "args": {"goal": "goto kitchen"}, "confidence": 0.9})] * 40
    rep = run(run_eval(ScriptedBackend(replies)))
    assert rep["summary"]["total"] == len(rep["interpreter"]) + len(rep["planner"])
    assert rep["interpreter"][0]["ok"]                                  # first case maps to plan/goto kitchen
    assert any(r["goal"].startswith("go to the stairs") and r["ok"] for r in rep["planner"])   # blocked by keep-out


@pytest.mark.skipif(not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
                    reason="live Claude eval needs API credentials")
def test_live_claude_eval():
    rep = run(run_eval(build_backend("anthropic", effort="low")))
    print(json.dumps(rep["summary"]))
    assert rep["summary"]["accuracy"] >= 0.7
