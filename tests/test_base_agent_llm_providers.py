from __future__ import annotations

import json

import pytest

from ledger.agents.base_agent import BaseApexAgent


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyAgent(BaseApexAgent):
    def build_graph(self):
        return None


@pytest.mark.asyncio
async def test_openrouter_path_uses_sk_or_key_from_gemini_env(monkeypatch):
    agent = _DummyAgent(
        agent_id="agent-test",
        agent_type="credit_analysis",
        store=None,
        registry=None,
        client=None,
        model="gemini-1.5-pro",
    )

    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-or-test-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer sk-or-test-key"
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["model"] == "google/gemini-2.5-flash"
        return _FakeResponse(
            {
                "choices": [
                    {"message": {"content": '{"status":"ok"}'}}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    text, tokens_in, tokens_out, cost = await agent._call_llm("system", "user", max_tokens=64)

    assert text == '{"status":"ok"}'
    assert tokens_in == 12
    assert tokens_out == 4
    assert cost == 0.0
