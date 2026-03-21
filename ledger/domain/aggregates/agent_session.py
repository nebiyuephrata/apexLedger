"""
ledger/domain/aggregates/agent_session.py
========================================
AgentSession aggregate. Replays session stream to rebuild state.
Enforces Gas Town anchor (AgentSessionStarted must be first event).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from ledger.domain.errors import DomainError


@dataclass
class AgentSessionAggregate:
    stream_id: str
    session_id: str | None = None
    agent_type: str | None = None
    application_id: str | None = None
    model_version: str | None = None
    started: bool = False
    completed: bool = False
    failed: bool = False
    node_count: int = 0
    outputs: list[dict[str, Any]] = field(default_factory=list)
    version: int = -1
    events: list[dict] = field(default_factory=list)

    @classmethod
    async def load(cls, store, stream_id: str) -> "AgentSessionAggregate":
        agg = cls(stream_id=stream_id, version=-1)
        events = await store.load_stream(stream_id)
        for event in events:
            agg._apply(event)
        if events:
            first = events[0].get("event_type")
            if first not in ("AgentSessionStarted", "AgentContextLoaded"):
                raise DomainError("AgentSessionStarted or AgentContextLoaded must be the first event in a session stream")
        return agg

    def _apply(self, event: dict) -> None:
        et = event.get("event_type")
        handler = getattr(self, f"_on_{et}", None)
        if handler:
            handler(event.get("payload", {}))
        if "stream_position" in event:
            self.version = event["stream_position"]
        else:
            self.version += 1
        self.events.append(event)

    # ─── EVENT HANDLERS (NO VALIDATION) ──────────────────────────────────────

    def _on_AgentSessionStarted(self, p: dict) -> None:
        self.started = True
        self.session_id = p.get("session_id")
        self.agent_type = p.get("agent_type")
        self.application_id = p.get("application_id")
        self.model_version = p.get("model_version")

    def _on_AgentNodeExecuted(self, p: dict) -> None:
        self.node_count += 1

    def _on_AgentOutputWritten(self, p: dict) -> None:
        self.outputs.append(p)

    def _on_AgentSessionCompleted(self, p: dict) -> None:
        self.completed = True

    def _on_AgentSessionFailed(self, p: dict) -> None:
        self.failed = True

    def has_output_for_application(self, application_id: str) -> bool:
        for out in self.outputs:
            if out.get("application_id") == application_id:
                return True
        return False
