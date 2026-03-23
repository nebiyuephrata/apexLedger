import pytest
from datetime import datetime

from ledger.event_store import InMemoryEventStore
from ledger.what_if import run_what_if


@pytest.mark.asyncio
async def test_what_if_skips_causal_events():
    store = InMemoryEventStore()
    app_id = "APP-WHATIF-1"
    stream = f"loan-{app_id}"

    # Append ApplicationSubmitted
    pos = await store.append(stream, [{"event_type": "ApplicationSubmitted", "event_version": 1, "payload": {
        "application_id": app_id,
        "applicant_id": "COMP-001",
        "requested_amount_usd": "100000",
        "loan_purpose": "working_capital",
        "loan_term_months": 24,
        "submission_channel": "web",
        "contact_email": "x@example.com",
        "contact_name": "X",
        "submitted_at": datetime.now().isoformat(),
        "application_reference": app_id,
    }}], expected_version=-1)

    # Append DecisionGenerated with causation_id of ApplicationSubmitted
    events = await store.load_stream(stream)
    first_id = events[-1]["event_id"]
    await store.append(stream, [{"event_type": "DecisionGenerated", "event_version": 2, "payload": {
        "application_id": app_id,
        "orchestrator_session_id": "sess-1",
        "recommendation": "APPROVE",
        "confidence": 0.8,
        "executive_summary": "ok",
        "contributing_sessions": [],
        "model_versions": {},
        "generated_at": datetime.now().isoformat(),
    }}], expected_version=0, causation_id=first_id)

    # Branch after first event, inject counterfactual DecisionGenerated
    cf_event = {"event_type": "DecisionGenerated", "event_version": 2, "payload": {
        "application_id": app_id,
        "orchestrator_session_id": "sess-cf",
        "recommendation": "DECLINE",
        "confidence": 0.9,
        "executive_summary": "cf",
        "contributing_sessions": [],
        "model_versions": {},
        "generated_at": datetime.now().isoformat(),
    }}

    result = await run_what_if(store, app_id, branch_global_position=0, counterfactual_events=[cf_event])

    assert result["real"]["decision"] == "APPROVE"
    assert result["simulated"]["decision"] == "DECLINE"
    assert len(result["skipped_event_ids"]) == 1
