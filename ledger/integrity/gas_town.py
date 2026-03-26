from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ledger.domain.errors import DomainError


class AgentContext(BaseModel):
    session_id: str | None = None
    agent_type: str | None = None
    application_id: str | None = None
    context_source: str | None = None
    started_at: datetime | None = None

    context_text: str = ""
    summary: str = ""
    recent_events: list[dict] = Field(default_factory=list)
    last_event_type: str | None = None
    last_event_position: int = 0
    last_node_name: str | None = None
    last_node_sequence: int | None = None
    completed_nodes: list[str] = Field(default_factory=list)
    last_output: dict | None = None

    pending_work: list[dict] = Field(default_factory=list)
    session_health_status: str = "HEALTHY"
    needs_reconciliation: bool = False


def _summarize_events(events: list[dict]) -> str:
    if not events:
        return ""
    counts: dict[str, int] = {}
    for ev in events:
        et = ev.get("event_type", "Unknown")
        counts[et] = counts.get(et, 0) + 1
    parts = [f"{k} x{v}" for k, v in sorted(counts.items())]
    return "; ".join(parts)


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    if isinstance(value, datetime):
        return value
    return None


async def reconstruct_agent_context(store, session_stream: str, keep_last_n: int = 3) -> AgentContext:
    """
    Replay a session stream and return a crash-recovery context.

    - Older events are summarized.
    - Last N events are preserved verbatim.
    - If the last event is not a terminal event, needs_reconciliation is True.
    """
    events = await store.load_stream(session_stream)
    if not events:
        raise DomainError("No events found for session stream", context={"stream_id": session_stream})

    first_type = events[0].get("event_type")
    if first_type not in ("AgentSessionStarted", "AgentContextLoaded"):
        raise DomainError("AgentSessionStarted or AgentContextLoaded must be first event", context={"stream_id": session_stream})

    preserve_types = {"AgentInputValidationFailed", "AgentSessionFailed"}
    preserved: list[dict] = []
    for idx, ev in enumerate(events):
        if keep_last_n > 0 and idx >= len(events) - keep_last_n:
            preserved.append(ev)
            continue
        if ev.get("event_type") in preserve_types:
            preserved.append(ev)

    # preserve order and remove duplicates
    seen_ids = set()
    recent: list[dict] = []
    for ev in preserved:
        ident = (ev.get("event_type"), ev.get("stream_position"), ev.get("global_position"), id(ev))
        if ident in seen_ids:
            continue
        seen_ids.add(ident)
        recent.append(ev)

    summary_events = [ev for ev in events if ev not in recent]
    summary = _summarize_events(summary_events)

    last_event = events[-1]
    last_event_type = last_event.get("event_type")

    terminal = {"AgentOutputWritten", "AgentSessionCompleted", "AgentSessionFailed"}
    needs_reconciliation = last_event_type not in terminal

    last_node_name = None
    last_node_sequence = None
    completed_nodes: list[str] = []
    last_output: dict | None = None
    for ev in reversed(events):
        if ev.get("event_type") == "AgentNodeExecuted":
            p = ev.get("payload", {})
            last_node_name = p.get("node_name")
            last_node_sequence = p.get("node_sequence")
            break
    for ev in events:
        if ev.get("event_type") == "AgentNodeExecuted":
            p = ev.get("payload", {})
            name = p.get("node_name")
            if name:
                completed_nodes.append(name)
        elif ev.get("event_type") == "AgentOutputWritten":
            last_output = ev.get("payload", {})

    first_payload = events[0].get("payload", {})
    last_event_position = events[-1].get("stream_position") if events else 0
    if last_event_position is None:
        last_event_position = len(events)
    else:
        # InMemoryEventStore uses zero-based positions; normalize to 1-based.
        if last_event_position == len(events) - 1:
            last_event_position += 1

    pending_work: list[dict] = []
    if needs_reconciliation:
        pending_work.append({
            "reason": "session_not_completed",
            "last_event_type": last_event_type,
            "last_node_name": last_node_name,
            "last_node_sequence": last_node_sequence,
        })

    context_text = summary
    if recent:
        recent_types = ", ".join([ev.get("event_type", "Unknown") for ev in recent])
        context_text = f"{summary}\\nRecent events: {recent_types}".strip()

    return AgentContext(
        session_id=first_payload.get("session_id"),
        agent_type=first_payload.get("agent_type"),
        application_id=first_payload.get("application_id"),
        context_source=first_payload.get("context_source"),
        started_at=_parse_ts(first_payload.get("started_at")),
        summary=summary,
        recent_events=recent,
        last_event_type=last_event_type,
        last_event_position=int(last_event_position),
        last_node_name=last_node_name,
        last_node_sequence=last_node_sequence,
        completed_nodes=completed_nodes,
        last_output=last_output,
        pending_work=pending_work,
        session_health_status="NEEDS_RECONCILIATION" if needs_reconciliation else "HEALTHY",
        needs_reconciliation=needs_reconciliation,
        context_text=context_text,
    )
