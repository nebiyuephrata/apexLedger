from __future__ import annotations

import pytest

import ledger.agents.base_agent as base_mod
from ledger.agents.base_agent import BaseApexAgent


class _DummyAgent(BaseApexAgent):
    def build_graph(self):
        agent = self

        class _Graph:
            async def ainvoke(self, state):
                await agent._record_tool_call("registry_lookup", {"application_id": state["application_id"]}, {"found": True}, 8)
                await agent._record_node_execution("validate_inputs", ["application_id"], ["validated"], 12)
                return {
                    **state,
                    "next_agent_triggered": "credit_analysis",
                    "output_events_written": ["CreditAnalysisRequested"],
                    "errors": [],
                }

        return _Graph()


class _FakeRun:
    def __init__(self, sink: list[dict], name: str, run_type: str, inputs: dict | None, tags: list[str] | None, metadata: dict | None):
        self._sink = sink
        self.name = name
        self.run_type = run_type
        self.inputs = inputs
        self.tags = tags or []
        self.metadata = metadata or {}
        self.outputs = {}
        self.error = None

    def add_outputs(self, outputs: dict):
        self.outputs.update(outputs)

    def __enter__(self):
        self._sink.append(
            {
                "name": self.name,
                "run_type": self.run_type,
                "inputs": self.inputs,
                "tags": self.tags,
                "metadata": self.metadata,
                "run": self,
            }
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None and self.error is None:
            self.error = f"{type(exc).__name__}: {exc}"
        return False


@pytest.mark.asyncio
async def test_process_application_emits_langsmith_root_and_child_spans(monkeypatch):
    spans: list[dict] = []

    def fake_trace(*, name, run_type, inputs=None, project_name=None, parent=None, tags=None, metadata=None, client=None):
        return _FakeRun(spans, name, run_type, inputs, tags, metadata)

    monkeypatch.setattr(base_mod, "LANGSMITH_AVAILABLE", True)
    monkeypatch.setattr(base_mod, "langsmith_trace", fake_trace)
    monkeypatch.setattr(base_mod, "LangSmithClient", lambda **kwargs: object())
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "apex-ledger-tests")

    agent = _DummyAgent(
        agent_id="agent-doc-1",
        agent_type="document_processing",
        store=None,
        registry=None,
        client=None,
        model="google/gemini-2.5-flash",
    )

    result = await agent.process_application("APP-TRACE-1")

    assert result["next_agent_triggered"] == "credit_analysis"
    names = [span["name"] for span in spans]
    assert "document_processing.process_application" in names
    assert "document_processing.registry_lookup" in names
    assert "document_processing.validate_inputs" in names

    root = next(span for span in spans if span["name"] == "document_processing.process_application")
    assert root["run"].outputs["next_agent_triggered"] == "credit_analysis"


@pytest.mark.asyncio
async def test_call_llm_emits_langsmith_llm_span(monkeypatch):
    spans: list[dict] = []

    def fake_trace(*, name, run_type, inputs=None, project_name=None, parent=None, tags=None, metadata=None, client=None):
        return _FakeRun(spans, name, run_type, inputs, tags, metadata)

    monkeypatch.setattr(base_mod, "LANGSMITH_AVAILABLE", True)
    monkeypatch.setattr(base_mod, "langsmith_trace", fake_trace)
    monkeypatch.setattr(base_mod, "LangSmithClient", lambda **kwargs: object())
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")

    agent = _DummyAgent(
        agent_id="agent-credit-1",
        agent_type="credit_analysis",
        store=None,
        registry=None,
        client=None,
        model="google/gemini-2.5-flash",
    )

    async def fake_openrouter(system: str, user: str, max_tokens: int = 1024):
        return '{"status":"ok"}', 11, 5, 0.0

    monkeypatch.setattr(agent, "_call_openrouter", fake_openrouter)

    text, tokens_in, tokens_out, cost = await agent._call_llm("system", "user", max_tokens=32)

    assert text == '{"status":"ok"}'
    assert tokens_in == 11
    assert tokens_out == 5
    assert cost == 0.0

    llm_span = next(span for span in spans if span["name"] == "credit_analysis.llm_call")
    assert llm_span["run_type"] == "llm"
    assert llm_span["run"].outputs["tokens_in"] == 11
    assert llm_span["run"].outputs["tokens_out"] == 5
