from __future__ import annotations

from datetime import datetime

import pytest

from ledger.agents.compliance_agent import ComplianceAgent
from ledger.event_store import InMemoryEventStore
from ledger.registry.client import CompanyProfile, ComplianceFlag
from ledger.schema.events import (
    ApplicationSubmitted,
    ComplianceCheckRequested,
    CreditAnalysisRequested,
    DocumentType,
    DocumentUploadRequested,
    DocumentUploaded,
    FraudScreeningRequested,
)


class FakeRegistry:
    def __init__(self, jurisdiction: str = "CA", founded_year: int = 2016, flags: list[ComplianceFlag] | None = None):
        self.jurisdiction = jurisdiction
        self.founded_year = founded_year
        self.flags = flags or []

    async def get_company(self, company_id: str) -> CompanyProfile | None:
        return CompanyProfile(
            company_id=company_id,
            name="Cedar Forge",
            industry="manufacturing",
            naics="332710",
            jurisdiction=self.jurisdiction,
            legal_type="LLC",
            founded_year=self.founded_year,
            employee_count=55,
            risk_segment="MIDDLE_MARKET",
            trajectory="STABLE",
            submission_channel="broker",
            ip_region="us-west",
        )

    async def get_compliance_flags(self, company_id: str, active_only: bool = False) -> list[ComplianceFlag]:
        if active_only:
            return [flag for flag in self.flags if flag.is_active]
        return list(self.flags)

    async def get_financial_history(self, company_id: str, years: list[int] | None = None) -> list[dict]:
        return []

    async def get_loan_relationships(self, company_id: str) -> list[dict]:
        return []


async def _append_event(store: InMemoryEventStore, stream_id: str, event: dict) -> None:
    version = await store.stream_version(stream_id)
    await store.append(stream_id=stream_id, events=[event], expected_version=version)


async def _seed_compliance_ready_application(store: InMemoryEventStore, app_id: str) -> None:
    await _append_event(
        store,
        f"loan-{app_id}",
        ApplicationSubmitted(
            application_id=app_id,
            applicant_id="COMP-COMP-1",
            requested_amount_usd="400000",
            loan_purpose="working_capital",
            loan_term_months=24,
            submission_channel="web",
            contact_email="compliance@test.local",
            contact_name="Cory Check",
            submitted_at=datetime.now(),
            application_reference=app_id,
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        DocumentUploadRequested(
            application_id=app_id,
            required_document_types=[DocumentType.INCOME_STATEMENT],
            deadline=datetime.now(),
            requested_by="system",
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        DocumentUploaded(
            application_id=app_id,
            document_id="doc-comp-income",
            document_type=DocumentType.INCOME_STATEMENT,
            document_format="pdf",
            filename="income.pdf",
            file_path="/tmp/income.pdf",
            file_size_bytes=1024,
            file_hash="hash-income",
            fiscal_year=2024,
            uploaded_at=datetime.now(),
            uploaded_by="user-1",
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        CreditAnalysisRequested(
            application_id=app_id,
            requested_at=datetime.now(),
            requested_by="document-agent",
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        FraudScreeningRequested(
            application_id=app_id,
            requested_at=datetime.now(),
            triggered_by_event_id="sess-fraud-seed",
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        ComplianceCheckRequested(
            application_id=app_id,
            requested_at=datetime.now(),
            triggered_by_event_id="sess-fraud-seed",
            regulation_set_version="2026-Q1",
            rules_to_evaluate=["REG-001", "REG-002", "REG-003", "REG-004", "REG-005", "REG-006"],
        ).to_store_dict(),
    )


@pytest.mark.asyncio
async def test_compliance_agent_hard_blocks_montana_and_declines_application():
    store = InMemoryEventStore()
    app_id = "APEX-COMP-001"
    await _seed_compliance_ready_application(store, app_id)

    agent = ComplianceAgent(
        agent_id="compliance-agent-1",
        agent_type="compliance",
        store=store,
        registry=FakeRegistry(jurisdiction="MT"),
        client=None,
        model="gemini-1.5-pro",
    )

    await agent.process_application(app_id)

    comp_events = await store.load_stream(f"compliance-{app_id}")
    loan_events = await store.load_stream(f"loan-{app_id}")
    session_events = await store.load_stream(agent._session_stream)

    assert comp_events[0]["event_type"] == "ComplianceCheckInitiated"
    failed = [event for event in comp_events if event["event_type"] == "ComplianceRuleFailed"]
    assert any(event["payload"]["rule_id"] == "REG-003" for event in failed)
    assert any(event["payload"]["is_hard_block"] for event in failed)
    assert comp_events[-1]["event_type"] == "ComplianceCheckCompleted"
    assert comp_events[-1]["payload"]["overall_verdict"] == "BLOCKED"
    assert any(event["event_type"] == "ApplicationDeclined" for event in loan_events)
    assert not any(event["event_type"] == "DecisionRequested" for event in loan_events)
    assert session_events[0]["event_type"] == "AgentSessionStarted"
    assert session_events[-1]["event_type"] == "AgentSessionCompleted"


@pytest.mark.asyncio
async def test_compliance_agent_requests_decision_when_no_hard_block_exists():
    store = InMemoryEventStore()
    app_id = "APEX-COMP-002"
    await _seed_compliance_ready_application(store, app_id)

    agent = ComplianceAgent(
        agent_id="compliance-agent-2",
        agent_type="compliance",
        store=store,
        registry=FakeRegistry(jurisdiction="CA"),
        client=None,
        model="gemini-1.5-pro",
    )

    await agent.process_application(app_id)

    comp_events = await store.load_stream(f"compliance-{app_id}")
    loan_events = await store.load_stream(f"loan-{app_id}")

    assert comp_events[-1]["event_type"] == "ComplianceCheckCompleted"
    assert comp_events[-1]["payload"]["overall_verdict"] == "CLEAR"
    assert any(event["event_type"] == "DecisionRequested" for event in loan_events)
    assert not any(event["event_type"] == "ApplicationDeclined" for event in loan_events)
