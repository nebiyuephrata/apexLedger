from __future__ import annotations

from datetime import datetime

import pytest

from ledger.agents.fraud_detection_agent import FraudDetectionAgent
from ledger.event_store import InMemoryEventStore
from ledger.registry.client import CompanyProfile, ComplianceFlag, FinancialYear
from ledger.schema.events import (
    ApplicationSubmitted,
    CreditAnalysisRequested,
    CreditDecision,
    CreditAnalysisCompleted,
    DocumentType,
    DocumentUploadRequested,
    DocumentUploaded,
    ExtractionCompleted,
    FinancialFacts,
    FraudScreeningRequested,
    PackageCreated,
    RiskTier,
)


class FakeRegistry:
    async def get_company(self, company_id: str) -> CompanyProfile | None:
        return CompanyProfile(
            company_id=company_id,
            name="Signal Works",
            industry="technology",
            naics="541511",
            jurisdiction="CA",
            legal_type="LLC",
            founded_year=2017,
            employee_count=42,
            risk_segment="SMB",
            trajectory="STABLE",
            submission_channel="direct",
            ip_region="us-west",
        )

    async def get_financial_history(self, company_id: str, years: list[int] | None = None) -> list[FinancialYear]:
        return [
            FinancialYear(
                fiscal_year=2024,
                total_revenue=1_000_000.0,
                gross_profit=410_000.0,
                operating_income=140_000.0,
                ebitda=170_000.0,
                net_income=100_000.0,
                total_assets=800_000.0,
                total_liabilities=320_000.0,
                total_equity=480_000.0,
                long_term_debt=110_000.0,
                cash_and_equivalents=90_000.0,
                current_assets=250_000.0,
                current_liabilities=120_000.0,
                accounts_receivable=80_000.0,
                inventory=25_000.0,
                debt_to_equity=0.67,
                current_ratio=2.08,
                debt_to_ebitda=0.65,
                interest_coverage_ratio=3.7,
                gross_margin=0.41,
                ebitda_margin=0.17,
                net_margin=0.10,
            )
        ]

    async def get_compliance_flags(self, company_id: str, active_only: bool = False) -> list[ComplianceFlag]:
        return []

    async def get_loan_relationships(self, company_id: str) -> list[dict]:
        return []


async def _append_event(store: InMemoryEventStore, stream_id: str, event: dict) -> None:
    version = await store.stream_version(stream_id)
    await store.append(stream_id=stream_id, events=[event], expected_version=version)


async def _seed_fraud_ready_application(store: InMemoryEventStore, app_id: str) -> None:
    await _append_event(
        store,
        f"loan-{app_id}",
        ApplicationSubmitted(
            application_id=app_id,
            applicant_id="COMP-FRAUD-1",
            requested_amount_usd="350000",
            loan_purpose="working_capital",
            loan_term_months=18,
            submission_channel="web",
            contact_email="fraud@test.local",
            contact_name="Fiona Signals",
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
            document_id="doc-fraud-income",
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
            triggered_by_event_id="sess-credit-seed",
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"credit-{app_id}",
        CreditAnalysisCompleted(
            application_id=app_id,
            session_id="sess-credit-seed",
            decision=CreditDecision(
                risk_tier=RiskTier.MEDIUM,
                recommended_limit_usd="250000",
                confidence=0.72,
                rationale="Seed credit decision.",
            ),
            model_version="gemini-1.5-pro",
            model_deployment_id="dep-creditseed",
            input_data_hash="seed-hash",
            analysis_duration_ms=25,
            completed_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"docpkg-{app_id}",
        PackageCreated(
            package_id=app_id,
            application_id=app_id,
            required_documents=[DocumentType.INCOME_STATEMENT],
            created_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"docpkg-{app_id}",
        ExtractionCompleted(
            package_id=app_id,
            document_id="doc-fraud-income",
            document_type=DocumentType.INCOME_STATEMENT,
            facts=FinancialFacts(
                total_revenue="2200000",
                total_assets="1000000",
                total_liabilities="400000",
                total_equity="500000",
                extraction_notes=["Submitted statements show sharp YoY growth."],
            ),
            raw_text_length=4500,
            tables_extracted=2,
            processing_ms=240,
            completed_at=datetime.now(),
        ).to_store_dict(),
    )


@pytest.mark.asyncio
async def test_fraud_agent_generates_events_and_triggers_compliance():
    store = InMemoryEventStore()
    app_id = "APEX-FRAUD-001"
    await _seed_fraud_ready_application(store, app_id)

    agent = FraudDetectionAgent(
        agent_id="fraud-agent-1",
        agent_type="fraud_detection",
        store=store,
        registry=FakeRegistry(),
        client=None,
        model="gemini-1.5-pro",
    )

    await agent.process_application(app_id)

    fraud_events = await store.load_stream(f"fraud-{app_id}")
    loan_events = await store.load_stream(f"loan-{app_id}")
    session_events = await store.load_stream(agent._session_stream)

    types = [event["event_type"] for event in fraud_events]
    assert types[0] == "FraudScreeningInitiated"
    assert "FraudAnomalyDetected" in types
    assert types[-1] == "FraudScreeningCompleted"
    assert fraud_events[-1]["payload"]["fraud_score"] > 0.60
    assert fraud_events[-1]["payload"]["recommendation"] == "DECLINE"
    assert any(event["event_type"] == "ComplianceCheckRequested" for event in loan_events)
    assert session_events[0]["event_type"] == "AgentSessionStarted"
    assert session_events[-1]["event_type"] == "AgentSessionCompleted"


@pytest.mark.asyncio
async def test_fraud_agent_requires_credit_analysis_completion():
    store = InMemoryEventStore()
    app_id = "APEX-FRAUD-002"
    await _append_event(
        store,
        f"loan-{app_id}",
        ApplicationSubmitted(
            application_id=app_id,
            applicant_id="COMP-FRAUD-2",
            requested_amount_usd="180000",
            loan_purpose="working_capital",
            loan_term_months=12,
            submission_channel="web",
            contact_email="fraud@test.local",
            contact_name="Gary Guard",
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
            document_id="doc-fraud-income-2",
            document_type=DocumentType.INCOME_STATEMENT,
            document_format="pdf",
            filename="income.pdf",
            file_path="/tmp/income.pdf",
            file_size_bytes=1024,
            file_hash="hash-income-2",
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
            triggered_by_event_id="sess-credit-seed",
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"docpkg-{app_id}",
        PackageCreated(
            package_id=app_id,
            application_id=app_id,
            required_documents=[DocumentType.INCOME_STATEMENT],
            created_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"docpkg-{app_id}",
        ExtractionCompleted(
            package_id=app_id,
            document_id="doc-fraud-income-2",
            document_type=DocumentType.INCOME_STATEMENT,
            facts=FinancialFacts(total_revenue="900000"),
            raw_text_length=4000,
            tables_extracted=1,
            processing_ms=180,
            completed_at=datetime.now(),
        ).to_store_dict(),
    )

    agent = FraudDetectionAgent(
        agent_id="fraud-agent-2",
        agent_type="fraud_detection",
        store=store,
        registry=FakeRegistry(),
        client=None,
        model="gemini-1.5-pro",
    )

    with pytest.raises(ValueError, match="Credit analysis must be completed"):
        await agent.process_application(app_id)

    fraud_events = await store.load_stream(f"fraud-{app_id}")
    session_events = await store.load_stream(agent._session_stream)
    assert fraud_events == []
    assert any(event["event_type"] == "AgentInputValidationFailed" for event in session_events)
    assert session_events[-1]["event_type"] == "AgentSessionFailed"
