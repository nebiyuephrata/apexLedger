from __future__ import annotations

from datetime import datetime
from typing import Any

from ledger.domain.errors import DomainError


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


async def reconstruct_agent_context(store, session_stream: str, keep_last_n: int = 3) -> dict:
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

    recent = events[-keep_last_n:] if keep_last_n > 0 else []
    summary = _summarize_events(events[:-keep_last_n]) if len(events) > keep_last_n else ""

    last_event = events[-1]
    last_event_type = last_event.get("event_type")

    terminal = {"AgentOutputWritten", "AgentSessionCompleted", "AgentSessionFailed"}
    needs_reconciliation = last_event_type not in terminal

    last_node_name = None
    last_node_sequence = None
    for ev in reversed(events):
        if ev.get("event_type") == "AgentNodeExecuted":
            p = ev.get("payload", {})
            last_node_name = p.get("node_name")
            last_node_sequence = p.get("node_sequence")
            break

    first_payload = events[0].get("payload", {})
    return {
        "session_id": first_payload.get("session_id"),
        "agent_type": first_payload.get("agent_type"),
        "application_id": first_payload.get("application_id"),
        "context_source": first_payload.get("context_source"),
        "summary": summary,
        "recent_events": recent,
        "last_event_type": last_event_type,
        "last_node_name": last_node_name,
        "last_node_sequence": last_node_sequence,
        "needs_reconciliation": needs_reconciliation,
        "started_at": _parse_ts(first_payload.get("started_at")),
    }
