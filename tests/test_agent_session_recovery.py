import pytest

from ledger.agents.base_agent import BaseApexAgent
from ledger.event_store import InMemoryEventStore


@pytest.mark.asyncio
async def test_reconstruct_agent_context_returns_last_node_and_output():
    store = InMemoryEventStore()
    stream_id = "agent-document-sess-1234"

    events = [
        {
            "event_type": "AgentSessionStarted",
            "event_version": 1,
            "payload": {
                "session_id": "sess-1234",
                "agent_type": "document_processing",
                "application_id": "APP-0001",
                "context_source": "fresh",
            },
        },
        {
            "event_type": "AgentNodeExecuted",
            "event_version": 1,
            "payload": {
                "session_id": "sess-1234",
                "agent_type": "document_processing",
                "node_name": "validate_inputs",
                "node_sequence": 1,
            },
        },
        {
            "event_type": "AgentNodeExecuted",
            "event_version": 1,
            "payload": {
                "session_id": "sess-1234",
                "agent_type": "document_processing",
                "node_name": "extract_income_statement",
                "node_sequence": 2,
            },
        },
        {
            "event_type": "AgentOutputWritten",
            "event_version": 1,
            "payload": {
                "session_id": "sess-1234",
                "agent_type": "document_processing",
                "application_id": "APP-0001",
                "events_written": [
                    {"stream_id": "docpkg-APP-0001", "event_type": "ExtractionCompleted"}
                ],
            },
        },
    ]

    version = -1
    for event in events:
        positions = await store.append(stream_id=stream_id, events=[event], expected_version=version)
        version = positions[-1]

    context = await BaseApexAgent.reconstruct_agent_context(store, stream_id)

    assert context["session_id"] == "sess-1234"
    assert context["agent_type"] == "document_processing"
    assert context["application_id"] == "APP-0001"
    assert context["context_source"] == "fresh"
    assert context["last_node_name"] == "extract_income_statement"
    assert context["last_node_sequence"] == 2
    assert context["completed_nodes"] == ["validate_inputs", "extract_income_statement"]
    assert context["last_output"]["events_written"][0]["event_type"] == "ExtractionCompleted"
    assert context["last_event_type"] == "AgentOutputWritten"
