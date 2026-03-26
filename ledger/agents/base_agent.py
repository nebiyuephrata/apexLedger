"""
ledger/agents/base_agent.py
===========================
Base LangGraph agent scaffolding shared by all Apex agents.
"""
from __future__ import annotations
import asyncio, hashlib, json, os, re, time, urllib.error, urllib.request
from contextlib import nullcontext
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import uuid4
from anthropic import AsyncAnthropic
from langgraph.graph import StateGraph, END

try:
    from langsmith import Client as LangSmithClient
    from langsmith import trace as langsmith_trace
    LANGSMITH_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    LangSmithClient = None
    langsmith_trace = None
    LANGSMITH_AVAILABLE = False

LANGGRAPH_VERSION = "1.0.0"
MAX_OCC_RETRIES = 5

class BaseApexAgent(ABC):
    """
    Base for all 5 Apex agents. Provides Gas Town session management,
    per-node event recording, tool call recording, OCC retry scaffolding.

    AGENT NODE SEQUENCE (all agents follow this):
        start_session → validate_inputs → load_context → [domain nodes] → write_output → end_session

    Each node must call self._record_node_execution() at its end.
    Each tool/registry call must call self._record_tool_call().
    The write_output node must call self._record_output_written() then self._record_node_execution().
    """
    def __init__(self, agent_id: str, agent_type: str, store, registry, client: AsyncAnthropic | Any | None, model="claude-sonnet-4-20250514"):
        self.agent_id = agent_id; self.agent_type = agent_type
        self.store = store; self.registry = registry; self.client = client; self.model = model
        self.session_id = None; self.application_id = None
        self._session_stream = None; self._t0 = None
        self._seq = 0; self._llm_calls = 0; self._tokens = 0; self._cost = 0.0
        self._session_version = -1
        self._graph = None
        self._session_started = False
        self._langsmith_client = None
        self._langsmith_root_run = None
        self._primary_stream_prefix = {
            "document_processing": "docpkg-",
            "credit_analysis": "credit-",
            "fraud_detection": "fraud-",
            "compliance": "compliance-",
            "decision_orchestrator": "loan-",
        }
        self._cross_stream_events = {
            "document_processing": {"loan-": {"CreditAnalysisRequested"}},
            "credit_analysis": {"loan-": {"FraudScreeningRequested"}},
            "fraud_detection": {"loan-": {"ComplianceCheckRequested"}},
            "compliance": {"loan-": {"DecisionRequested", "ApplicationDeclined"}},
        }

    @abstractmethod
    def build_graph(self): raise NotImplementedError

    async def process_application(self, application_id: str, session_id: str | None = None, context_source: str = "fresh") -> dict:
        if not self._graph: self._graph = self.build_graph()
        self.application_id = application_id
        self.session_id = session_id or f"sess-{self.agent_type[:3]}-{uuid4().hex[:8]}"
        self._session_stream = f"agent-{self.agent_type}-{self.session_id}"
        self._t0 = time.time(); self._seq = 0; self._llm_calls = 0; self._tokens = 0; self._cost = 0.0
        self._session_started = False; self._session_version = -1
        if self.store:
            self._session_version = await self.store.stream_version(self._session_stream)
            if self._session_version >= 0:
                self._session_started = True
        trace_ctx = self._langsmith_trace(
            name=f"{self.agent_type}.process_application",
            run_type="chain",
            inputs={
                "application_id": application_id,
                "session_id": self.session_id,
                "context_source": context_source,
                "agent_id": self.agent_id,
                "agent_type": self.agent_type,
                "model": self.model,
            },
            tags=["apex-ledger", "agent-run", self.agent_type],
            metadata={"langgraph_graph_version": LANGGRAPH_VERSION},
        )
        with trace_ctx as root_run:
            self._langsmith_root_run = root_run
            try:
                if not self._session_started:
                    await self._start_session(application_id, context_source=context_source)
                result = await self._graph.ainvoke(self._initial_state(application_id))
                await self._complete_session(result)
                if root_run is not None:
                    root_run.add_outputs(
                        {
                            "next_agent_triggered": result.get("next_agent_triggered"),
                            "output_events_written": result.get("output_events_written", []),
                            "errors": result.get("errors", []),
                        }
                    )
                return result
            except Exception as e:
                if root_run is not None:
                    root_run.error = f"{type(e).__name__}: {e}"
                await self._fail_session(type(e).__name__, str(e))
                raise
            finally:
                self._langsmith_root_run = None

    def _initial_state(self, app_id):
        return {"application_id": app_id, "session_id": self.session_id,
                "agent_id": self.agent_id, "errors": [], "output_events_written": [], "next_agent_triggered": None}

    async def _start_session(self, app_id, context_source: str = "fresh"):
        await self._append_session({"event_type":"AgentSessionStarted","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"agent_id":self.agent_id,
            "application_id":app_id,"model_version":self.model,"langgraph_graph_version":LANGGRAPH_VERSION,
            "context_source":context_source,"context_token_count":1000,"started_at":datetime.now().isoformat()}})

    async def _record_node_execution(self, name, in_keys, out_keys, ms, tok_in=None, tok_out=None, cost=None):
        self._seq += 1
        if tok_in: self._tokens += tok_in + (tok_out or 0); self._llm_calls += 1
        if cost: self._cost += cost
        await self._append_session({"event_type":"AgentNodeExecuted","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"node_name":name,
            "node_sequence":self._seq,"input_keys":in_keys,"output_keys":out_keys,
            "llm_called":tok_in is not None,"llm_tokens_input":tok_in,"llm_tokens_output":tok_out,
            "llm_cost_usd":cost,"duration_ms":ms,"executed_at":datetime.now().isoformat()}})
        self._record_langsmith_span(
            name=f"{self.agent_type}.{name}",
            run_type="chain",
            inputs={"input_keys": in_keys},
            outputs={
                "output_keys": out_keys,
                "duration_ms": ms,
                "llm_called": tok_in is not None,
                "llm_tokens_input": tok_in,
                "llm_tokens_output": tok_out,
                "llm_cost_usd": cost,
            },
            tags=["agent-node", self.agent_type, name],
            metadata={"node_sequence": self._seq},
        )

    async def _record_input_validated(self, inputs_validated: list[str], ms: int):
        await self._append_session({"event_type":"AgentInputValidated","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"application_id":self.application_id,
            "inputs_validated":inputs_validated,"validation_duration_ms":ms,"validated_at":datetime.now().isoformat()}})

    async def _record_input_failed(self, missing_inputs: list[str], errors: list[str]):
        await self._append_session({"event_type":"AgentInputValidationFailed","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"application_id":self.application_id,
            "missing_inputs":missing_inputs,"validation_errors":errors,"failed_at":datetime.now().isoformat()}})

    async def _record_tool_call(self, tool, inp, out, ms):
        await self._append_session({"event_type":"AgentToolCalled","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"tool_name":tool,
            "tool_input_summary":inp,"tool_output_summary":out,"tool_duration_ms":ms,
            "called_at":datetime.now().isoformat()}})
        self._record_langsmith_span(
            name=f"{self.agent_type}.{tool}",
            run_type="tool",
            inputs={"tool_input_summary": inp},
            outputs={"tool_output_summary": out, "tool_duration_ms": ms},
            tags=["agent-tool", self.agent_type, tool],
        )

    async def _record_output_written(self, events_written, summary):
        await self._append_session({"event_type":"AgentOutputWritten","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"application_id":self.application_id,
            "events_written":events_written,"output_summary":summary,"written_at":datetime.now().isoformat()}})

    async def _complete_session(self, result):
        ms = int((time.time()-self._t0)*1000)
        await self._append_session({"event_type":"AgentSessionCompleted","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"application_id":self.application_id,
            "total_nodes_executed":self._seq,"total_llm_calls":self._llm_calls,"total_tokens_used":self._tokens,
            "total_cost_usd":round(self._cost,6),"total_duration_ms":ms,
            "next_agent_triggered":result.get("next_agent_triggered"),"completed_at":datetime.now().isoformat()}})

    async def _fail_session(self, etype, emsg):
        await self._append_session({"event_type":"AgentSessionFailed","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"application_id":self.application_id,
            "error_type":etype,"error_message":emsg[:500],"last_successful_node":f"node_{self._seq}",
            "recoverable":etype in ("llm_timeout","RateLimitError"),"failed_at":datetime.now().isoformat()}})

    async def _append_session(self, event: dict):
        """Append to AgentSession stream with ordering enforcement."""
        etype = event.get("event_type")
        if not self._session_started:
            if etype != "AgentSessionStarted":
                raise ValueError("AgentSessionStarted must be the first event in a session stream")
            self._session_started = True
        elif etype == "AgentSessionStarted":
            raise ValueError("AgentSessionStarted can only appear once per session stream")
        if not self.store:
            print(f"  [{self.agent_type[:8]}:{self.session_id}] {event['event_type']}")
            return
        positions = await self.store.append(
            stream_id=self._session_stream,
            events=[event],
            expected_version=self._session_version,
        )
        if positions:
            self._session_version = positions[-1]

    async def _append_stream(self, stream_id: str, event_dict: dict, causation_id: str = None):
        """Append to any aggregate stream with OCC retry."""
        self._ensure_write_access(stream_id, [event_dict])
        for attempt in range(MAX_OCC_RETRIES):
            try:
                ver = await self.store.stream_version(stream_id)
                await self.store.append(stream_id=stream_id, events=[event_dict],
                    expected_version=ver, causation_id=causation_id)
                return
            except Exception as e:
                if "OptimisticConcurrencyError" in type(e).__name__ and attempt < MAX_OCC_RETRIES-1:
                    await asyncio.sleep(0.1 * (2**attempt)); continue
                raise

    async def _append_with_retry(self, stream_id: str, events: list[dict], causation_id: str | None = None, metadata: dict | None = None) -> list[int]:
        """Append to any stream with OCC retry; returns positions."""
        self._ensure_write_access(stream_id, events)
        for attempt in range(MAX_OCC_RETRIES):
            try:
                ver = await self.store.stream_version(stream_id)
                return await self.store.append(
                    stream_id=stream_id,
                    events=events,
                    expected_version=ver,
                    causation_id=causation_id,
                    metadata=metadata,
                )
            except Exception as e:
                if "OptimisticConcurrencyError" in type(e).__name__ and attempt < MAX_OCC_RETRIES - 1:
                    await asyncio.sleep(0.1 * (2**attempt)); continue
                raise

    def _ensure_write_access(self, stream_id: str, events: list[dict]) -> None:
        """Enforce agent write boundaries to aggregate streams."""
        prefix = stream_id.split("-", 1)[0] + "-"
        primary_prefix = self._primary_stream_prefix.get(self.agent_type)
        if primary_prefix and prefix == primary_prefix:
            return
        # Allow explicit cross-stream triggers
        allowed = self._cross_stream_events.get(self.agent_type, {})
        allowed_types = allowed.get(prefix, set())
        for ev in events:
            et = ev.get("event_type")
            if et not in allowed_types:
                raise ValueError(
                    f"Agent '{self.agent_type}' cannot append {et} to stream '{stream_id}'"
                )

    async def _call_llm(self, system, user, max_tokens=1024):
        provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
        trace_ctx = self._langsmith_trace(
            name=f"{self.agent_type}.llm_call",
            run_type="llm",
            inputs={"system": system, "user": user, "max_tokens": max_tokens, "provider": provider or "auto"},
            tags=["llm", self.agent_type],
            metadata={"model": self.model},
        )
        with trace_ctx as llm_run:
            try:
                if self.client is not None and hasattr(self.client, "messages"):
                    resp = await self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        system=system,
                        messages=[{"role":"user","content":user}],
                    )
                    t = resp.content[0].text
                    i = resp.usage.input_tokens
                    o = resp.usage.output_tokens
                    result = (t, i, o, round(i/1e6*3.0 + o/1e6*15.0, 6))
                elif self._should_use_openrouter(provider):
                    result = await self._call_openrouter(system, user, max_tokens=max_tokens)
                elif provider == "gemini" or self.model.startswith("gemini"):
                    result = await self._call_gemini(system, user, max_tokens=max_tokens)
                else:
                    raise RuntimeError(
                        "No LLM client configured. Provide an Anthropic client, configure OpenRouter, or set LLM_PROVIDER=gemini with GEMINI_API_KEY.",
                    )
                if llm_run is not None:
                    content, tokens_in, tokens_out, cost = result
                    llm_run.add_outputs(
                        {
                            "content_preview": content[:200],
                            "tokens_in": tokens_in,
                            "tokens_out": tokens_out,
                            "cost_usd": cost,
                        }
                    )
                return result
            except Exception as exc:
                if llm_run is not None:
                    llm_run.error = f"{type(exc).__name__}: {exc}"
                raise

    @staticmethod
    def _should_use_openrouter(provider: str) -> bool:
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if provider == "openrouter" or bool(openrouter_key):
            return True
        # Support current local setup where an OpenRouter key was placed in GEMINI_API_KEY.
        return gemini_key.startswith("sk-or-")

    def _resolve_openrouter_key(self) -> str:
        key = os.environ.get("OPENROUTER_API_KEY")
        if key:
            return key
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key.startswith("sk-or-"):
            return gemini_key
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    def _resolve_openrouter_model(self) -> str:
        model = os.environ.get("OPENROUTER_MODEL")
        if model:
            return model
        if "/" in self.model:
            return self.model
        if self.model.startswith("gemini"):
            return f"google/{self.model}"
        gemini_model = os.environ.get("GEMINI_MODEL")
        if gemini_model:
            return gemini_model if "/" in gemini_model else f"google/{gemini_model}"
        return "google/gemini-2.5-flash"

    async def _call_openrouter(self, system: str, user: str, max_tokens: int = 1024):
        api_key = self._resolve_openrouter_key()
        model = self._resolve_openrouter_model()
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if os.environ.get("OPENROUTER_SITE_URL"):
            headers["HTTP-Referer"] = os.environ["OPENROUTER_SITE_URL"]
        if os.environ.get("OPENROUTER_APP_NAME"):
            headers["X-Title"] = os.environ["OPENROUTER_APP_NAME"]
        url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"

        def _request() -> tuple[str, int, int, float]:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"OpenRouter API error {exc.code}: {detail}") from exc

            choices = raw.get("choices") or []
            if not choices:
                raise RuntimeError(f"OpenRouter returned no choices: {raw}")
            message = choices[0].get("message", {}) or {}
            text = message.get("content", "")
            usage = raw.get("usage") or {}
            tokens_in = int(usage.get("prompt_tokens", 0))
            tokens_out = int(usage.get("completion_tokens", 0))
            return text, tokens_in, tokens_out, 0.0

        return await asyncio.to_thread(_request)

    async def _call_gemini(self, system: str, user: str, max_tokens: int = 1024):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")

        model = self.model if self.model.startswith("gemini") else os.environ.get("GEMINI_MODEL", "gemini-1.5-pro")
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        def _request() -> tuple[str, int, int, float]:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"Gemini API error {exc.code}: {detail}") from exc

            candidates = raw.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"Gemini returned no candidates: {raw}")

            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts)
            usage = raw.get("usageMetadata") or {}
            tokens_in = int(usage.get("promptTokenCount", 0))
            tokens_out = int(usage.get("candidatesTokenCount", 0))
            return text, tokens_in, tokens_out, 0.0

        return await asyncio.to_thread(_request)

    @staticmethod
    def _parse_json(content: str) -> dict:
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise ValueError("LLM response did not contain a JSON object")
        parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("LLM response JSON was not an object")
        return parsed

    @staticmethod
    def _sha(d): return hashlib.sha256(json.dumps(str(d),sort_keys=True).encode()).hexdigest()[:16]

    def _langsmith_enabled(self) -> bool:
        if not LANGSMITH_AVAILABLE:
            return False
        api_key = (os.environ.get("LANGSMITH_API_KEY") or "").strip()
        tracing_flag = (os.environ.get("LANGCHAIN_TRACING_V2") or "").strip().lower()
        return bool(api_key) and tracing_flag not in {"", "0", "false", "no", "off"}

    def _get_langsmith_client(self):
        if not self._langsmith_enabled():
            return None
        if self._langsmith_client is None:
            self._langsmith_client = LangSmithClient(
                api_key=os.environ.get("LANGSMITH_API_KEY"),
                api_url=os.environ.get("LANGCHAIN_ENDPOINT") or None,
            )
        return self._langsmith_client

    def _langsmith_project(self) -> str | None:
        return (os.environ.get("LANGCHAIN_PROJECT") or "apex-ledger").strip() or "apex-ledger"

    def _langsmith_trace(self, *, name: str, run_type: str, inputs: dict | None = None, tags: list[str] | None = None, metadata: dict | None = None):
        if not self._langsmith_enabled():
            return nullcontext(None)
        try:
            return langsmith_trace(
                name=name,
                run_type=run_type,
                inputs=inputs,
                project_name=self._langsmith_project(),
                parent=self._langsmith_root_run,
                tags=tags,
                metadata=metadata,
                client=self._get_langsmith_client(),
            )
        except Exception:
            return nullcontext(None)

    def _record_langsmith_span(
        self,
        *,
        name: str,
        run_type: str,
        inputs: dict | None = None,
        outputs: dict | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> None:
        trace_ctx = self._langsmith_trace(
            name=name,
            run_type=run_type,
            inputs=inputs,
            tags=tags,
            metadata=metadata,
        )
        try:
            with trace_ctx as run:
                if run is not None and outputs is not None:
                    run.add_outputs(outputs)
        except Exception:
            # Tracing must never break the agent pipeline.
            return

    @staticmethod
    async def reconstruct_agent_context(store, session_stream: str):
        """
        Replay an AgentSession stream and return a typed AgentContext.
        Delegates to the Gas Town reconstruction logic for a consistent contract.
        """
        from ledger.integrity.gas_town import reconstruct_agent_context
        return await reconstruct_agent_context(store, session_stream)
