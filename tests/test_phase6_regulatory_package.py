import pytest
from datetime import datetime, timedelta

from ledger.event_store import InMemoryEventStore
from ledger.regulatory_package import generate_regulatory_package


@pytest.mark.asyncio
async def test_generate_regulatory_package_contains_expected_sections():
    store = InMemoryEventStore()
    app_id = "APP-REG-1"

    await store.append(f"loan-{app_id}", [{"event_type": "ApplicationSubmitted", "event_version": 1, "payload": {
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

    await store.append(f"compliance-{app_id}", [{"event_type": "ComplianceCheckCompleted", "event_version": 1, "payload": {
        "application_id": app_id,
        "session_id": "sess-1",
        "rules_evaluated": 1,
        "rules_passed": 1,
        "rules_failed": 0,
        "rules_noted": 0,
        "has_hard_block": False,
        "overall_verdict": "CLEAR",
        "completed_at": datetime.now().isoformat(),
    }}], expected_version=-1)

    package = await generate_regulatory_package(store, app_id, datetime.now() + timedelta(seconds=1))

    assert package["application_id"] == app_id
    assert "events" in package
    assert "projections" in package
    assert "audit_chain" in package
    assert "narrative" in package
    assert isinstance(package["narrative"], list)
