import pytest

from ledger.event_store import InMemoryEventStore
from ledger.integrity.gas_town import reconstruct_agent_context


@pytest.mark.asyncio
async def test_gas_town_recovery_flags_needs_reconciliation():
    store = InMemoryEventStore()
    stream_id = "agent-credit_analysis-sess-gt1"

    events = [
        {"event_type": "AgentSessionStarted", "event_version": 1, "payload": {
            "session_id": "sess-gt1",
            "agent_type": "credit_analysis",
            "application_id": "APP-1",
            "model_version": "m1",
            "context_source": "fresh",
            "started_at": "2026-03-23T10:00:00",
        }},
        {"event_type": "AgentNodeExecuted", "event_version": 1, "payload": {
            "session_id": "sess-gt1",
            "agent_type": "credit_analysis",
            "node_name": "analyze_credit_risk",
            "node_sequence": 1,
        }},
    ]

    version = -1
    for ev in events:
        positions = await store.append(stream_id, [ev], expected_version=version)
        version = positions[-1]

    ctx = await reconstruct_agent_context(store, stream_id, keep_last_n=3)
    assert ctx["needs_reconciliation"] is True
    assert ctx["last_node_name"] == "analyze_credit_risk"
    assert ctx["summary"] == ""
    assert len(ctx["recent_events"]) == 2
