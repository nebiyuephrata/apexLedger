"""
ledger/agents/base_agent.py
===========================
Base LangGraph agent scaffolding shared by all Apex agents.
"""
from __future__ import annotations
import asyncio, hashlib, json, time
from abc import ABC, abstractmethod
from datetime import datetime
from uuid import uuid4
from anthropic import AsyncAnthropic
from langgraph.graph import StateGraph, END

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
    def __init__(self, agent_id: str, agent_type: str, store, registry, client: AsyncAnthropic, model="claude-sonnet-4-20250514"):
        self.agent_id = agent_id; self.agent_type = agent_type
        self.store = store; self.registry = registry; self.client = client; self.model = model
        self.session_id = None; self.application_id = None
        self._session_stream = None; self._t0 = None
        self._seq = 0; self._llm_calls = 0; self._tokens = 0; self._cost = 0.0
        self._session_version = -1
        self._graph = None
        self._session_started = False
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

    async def process_application(self, application_id: str) -> None:
        if not self._graph: self._graph = self.build_graph()
        self.application_id = application_id
        self.session_id = f"sess-{self.agent_type[:3]}-{uuid4().hex[:8]}"
        self._session_stream = f"agent-{self.agent_type}-{self.session_id}"
        self._t0 = time.time(); self._seq = 0; self._llm_calls = 0; self._tokens = 0; self._cost = 0.0
        self._session_started = False; self._session_version = -1
        await self._start_session(application_id)
        try:
            result = await self._graph.ainvoke(self._initial_state(application_id))
            await self._complete_session(result)
        except Exception as e:
            await self._fail_session(type(e).__name__, str(e)); raise

    def _initial_state(self, app_id):
        return {"application_id": app_id, "session_id": self.session_id,
                "agent_id": self.agent_id, "errors": [], "output_events_written": [], "next_agent_triggered": None}

    async def _start_session(self, app_id):
        await self._append_session({"event_type":"AgentSessionStarted","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"agent_id":self.agent_id,
            "application_id":app_id,"model_version":self.model,"langgraph_graph_version":LANGGRAPH_VERSION,
            "context_source":"fresh","context_token_count":1000,"started_at":datetime.now().isoformat()}})

    async def _record_node_execution(self, name, in_keys, out_keys, ms, tok_in=None, tok_out=None, cost=None):
        self._seq += 1
        if tok_in: self._tokens += tok_in + (tok_out or 0); self._llm_calls += 1
        if cost: self._cost += cost
        await self._append_session({"event_type":"AgentNodeExecuted","event_version":1,"payload":{
            "session_id":self.session_id,"agent_type":self.agent_type,"node_name":name,
            "node_sequence":self._seq,"input_keys":in_keys,"output_keys":out_keys,
            "llm_called":tok_in is not None,"llm_tokens_input":tok_in,"llm_tokens_output":tok_out,
            "llm_cost_usd":cost,"duration_ms":ms,"executed_at":datetime.now().isoformat()}})

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
        resp = await self.client.messages.create(model=self.model, max_tokens=max_tokens,
            system=system, messages=[{"role":"user","content":user}])
        t = resp.content[0].text; i = resp.usage.input_tokens; o = resp.usage.output_tokens
        return t, i, o, round(i/1e6*3.0 + o/1e6*15.0, 6)

    @staticmethod
    def _sha(d): return hashlib.sha256(json.dumps(str(d),sort_keys=True).encode()).hexdigest()[:16]

    @staticmethod
    async def reconstruct_agent_context(store, session_stream: str) -> dict:
        """
        Replay an AgentSession stream and return a resume context.
        Returns:
            {
              "session_id": str,
              "agent_type": str,
              "application_id": str,
              "context_source": str,
              "last_node_name": str | None,
              "last_node_sequence": int | None,
              "completed_nodes": list[str],
              "last_output": dict | None,
              "last_event_type": str | None,
            }
        """
        events = await store.load_stream(session_stream)
        if not events:
            raise ValueError(f"No events found for session stream: {session_stream}")

        first_type = events[0].get("event_type")
        if first_type not in ("AgentSessionStarted", "AgentContextLoaded"):
            raise ValueError("AgentSessionStarted or AgentContextLoaded must be the first event in a session stream")

        session_id = None
        agent_type = None
        application_id = None
        context_source = None
        last_node = None
        completed_nodes: list[str] = []
        last_output = None
        last_event_type = None

        for ev in events:
            et = ev.get("event_type")
            last_event_type = et
            p = ev.get("payload", {})
            if et in ("AgentSessionStarted", "AgentContextLoaded"):
                session_id = p.get("session_id") or session_id
                agent_type = p.get("agent_type") or agent_type
                application_id = p.get("application_id") or application_id
                context_source = p.get("context_source") or context_source
            elif et == "AgentNodeExecuted":
                name = p.get("node_name")
                seq = p.get("node_sequence")
                if name:
                    completed_nodes.append(name)
                if last_node is None or (seq is not None and seq >= (last_node.get("node_sequence") or -1)):
                    last_node = {"node_name": name, "node_sequence": seq}
            elif et == "AgentOutputWritten":
                last_output = p

        return {
            "session_id": session_id,
            "agent_type": agent_type,
            "application_id": application_id,
            "context_source": context_source,
            "last_node_name": last_node.get("node_name") if last_node else None,
            "last_node_sequence": last_node.get("node_sequence") if last_node else None,
            "completed_nodes": completed_nodes,
            "last_output": last_output,
            "last_event_type": last_event_type,
        }
