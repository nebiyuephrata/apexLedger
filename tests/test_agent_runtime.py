from __future__ import annotations

from datetime import datetime

import pytest

from ledger.agents.runtime import (
    run_compliance_agent,
    run_credit_analysis_agent,
    run_fraud_detection_agent,
)
from ledger.event_store import InMemoryEventStore
from ledger.registry.client import CompanyProfile, ComplianceFlag, FinancialYear
from ledger.schema.events import (
    ApplicationSubmitted,
    ComplianceCheckRequested,
    CreditAnalysisRequested,
    CreditAnalysisCompleted,
    CreditDecision,
    DocumentAdded,
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
    def __init__(self, jurisdiction: str = "CA", flags: list[ComplianceFlag] | None = None):
        self.jurisdiction = jurisdiction
        self.flags = flags or []

    async def get_company(self, company_id: str) -> CompanyProfile | None:
        return CompanyProfile(
            company_id=company_id,
            name="Runtime Metals",
            industry="manufacturing",
            naics="332999",
            jurisdiction=self.jurisdiction,
            legal_type="LLC",
            founded_year=2014,
            employee_count=75,
            risk_segment="SMB",
            trajectory="STABLE",
            submission_channel="direct",
            ip_region="us-west",
        )

    async def get_financial_history(self, company_id: str, years: list[int] | None = None) -> list[FinancialYear]:
        return [
            FinancialYear(
                fiscal_year=2024,
                total_revenue=900_000.0,
                gross_profit=360_000.0,
                operating_income=140_000.0,
                ebitda=180_000.0,
                net_income=105_000.0,
                total_assets=780_000.0,
                total_liabilities=320_000.0,
                total_equity=460_000.0,
                long_term_debt=120_000.0,
                cash_and_equivalents=85_000.0,
                current_assets=255_000.0,
                current_liabilities=125_000.0,
                accounts_receivable=92_000.0,
                inventory=48_000.0,
                debt_to_equity=0.70,
                current_ratio=2.04,
                debt_to_ebitda=0.67,
                interest_coverage_ratio=3.6,
                gross_margin=0.40,
                ebitda_margin=0.20,
                net_margin=0.12,
            )
        ]

    async def get_compliance_flags(self, company_id: str, active_only: bool = False) -> list[ComplianceFlag]:
        if active_only:
            return [flag for flag in self.flags if flag.is_active]
        return list(self.flags)

    async def get_loan_relationships(self, company_id: str) -> list[dict]:
        return []


class FakeMessagesAPI:
    async def create(self, **_: object):
        class FakeResponse:
            class Usage:
                input_tokens = 100
                output_tokens = 60

            class Block:
                text = """
                {
                  "risk_tier": "LOW",
                  "recommended_limit_usd": 200000,
                  "confidence": 0.81,
                  "rationale": "Healthy margin profile and stable leverage.",
                  "key_concerns": [],
                  "data_quality_caveats": [],
                  "policy_overrides_applied": []
                }
                """

            content = [Block()]
            usage = Usage()

        return FakeResponse()


class FakeLLMClient:
    def __init__(self):
        self.messages = FakeMessagesAPI()


async def _append_event(store: InMemoryEventStore, stream_id: str, event: dict) -> None:
    version = await store.stream_version(stream_id)
    await store.append(stream_id=stream_id, events=[event], expected_version=version)


async def _seed_credit_ready_application(store: InMemoryEventStore, app_id: str) -> None:
    await _append_event(
        store,
        f"loan-{app_id}",
        ApplicationSubmitted(
            application_id=app_id,
            applicant_id="COMP-RUNTIME-1",
            requested_amount_usd="300000",
            loan_purpose="working_capital",
            loan_term_months=18,
            submission_channel="web",
            contact_email="runtime@test.local",
            contact_name="Rita Runtime",
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
            document_id="doc-runtime-income",
            document_type=DocumentType.INCOME_STATEMENT,
            document_format="pdf",
            filename="income.pdf",
            file_path="/tmp/income.pdf",
            file_size_bytes=1024,
            file_hash="hash-runtime-income",
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
        DocumentAdded(
            package_id=app_id,
            document_id="doc-runtime-income",
            document_type=DocumentType.INCOME_STATEMENT,
            document_format="pdf",
            file_hash="hash-runtime-income",
            added_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"docpkg-{app_id}",
        ExtractionCompleted(
            package_id=app_id,
            document_id="doc-runtime-income",
            document_type=DocumentType.INCOME_STATEMENT,
            facts=FinancialFacts(
                total_revenue="900000",
                total_assets="780000",
                total_liabilities="320000",
                total_equity="460000",
                net_income="105000",
                ebitda="180000",
            ),
            raw_text_length=4200,
            tables_extracted=2,
            processing_ms=190,
            completed_at=datetime.now(),
        ).to_store_dict(),
    )


async def _seed_fraud_ready_application(store: InMemoryEventStore, app_id: str) -> None:
    await _seed_credit_ready_application(store, app_id)
    await _append_event(
        store,
        f"credit-{app_id}",
        CreditAnalysisCompleted(
            application_id=app_id,
            session_id="sess-credit-seed",
            decision=CreditDecision(
                risk_tier=RiskTier.MEDIUM,
                recommended_limit_usd="200000",
                confidence=0.81,
                rationale="Seed credit decision.",
            ),
            model_version="gemini-1.5-pro",
            model_deployment_id="dep-credit-seed",
            input_data_hash="seed-hash",
            analysis_duration_ms=25,
            completed_at=datetime.now(),
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


async def _seed_compliance_ready_application(store: InMemoryEventStore, app_id: str) -> None:
    await _seed_fraud_ready_application(store, app_id)
    await _append_event(
        store,
        f"fraud-{app_id}",
        {
            "event_type": "FraudScreeningCompleted",
            "event_version": 1,
            "payload": {
                "application_id": app_id,
                "session_id": "sess-fraud-seed",
                "fraud_score": 0.12,
                "risk_level": "LOW",
                "anomalies_found": 0,
                "recommendation": "CLEAR",
                "screening_model_version": "gemini-1.5-pro",
                "input_data_hash": "fraud-hash",
                "completed_at": datetime.now().isoformat(),
            },
        },
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
async def test_runtime_credit_runner_reuses_existing_session_stream():
    store = InMemoryEventStore()
    app_id = "APEX-RUNTIME-001"
    session_id = "sess-credit-runtime"
    await _seed_credit_ready_application(store, app_id)

    await _append_event(
        store,
        f"agent-credit_analysis-{session_id}",
        {
            "event_type": "AgentSessionStarted",
            "event_version": 1,
            "payload": {
                "session_id": session_id,
                "agent_type": "credit_analysis",
                "agent_id": "agent-credit-runtime",
                "application_id": app_id,
                "model_version": "gemini-1.5-pro",
                "langgraph_graph_version": "1.0.0",
                "context_source": "fresh",
                "context_token_count": 100,
                "started_at": datetime.now().isoformat(),
            },
        },
    )

    result = await run_credit_analysis_agent(
        store=store,
        registry=FakeRegistry(),
        application_id=app_id,
        agent_id="agent-credit-runtime",
        model="gemini-1.5-pro",
        client=FakeLLMClient(),
        session_id=session_id,
        context_source="prior_session_replay:sess-credit-runtime",
    )

    session_events = await store.load_stream(f"agent-credit_analysis-{session_id}")
    started = [event for event in session_events if event["event_type"] == "AgentSessionStarted"]
    assert result["session_id"] == session_id
    assert len(started) == 1
    assert any(event["event_type"] == "AgentSessionCompleted" for event in session_events)


@pytest.mark.asyncio
async def test_runtime_fraud_runner_reuses_existing_session_stream():
    store = InMemoryEventStore()
    app_id = "APEX-RUNTIME-002"
    session_id = "sess-fraud-runtime"
    await _seed_fraud_ready_application(store, app_id)

    await _append_event(
        store,
        f"agent-fraud_detection-{session_id}",
        {
            "event_type": "AgentSessionStarted",
            "event_version": 1,
            "payload": {
                "session_id": session_id,
                "agent_type": "fraud_detection",
                "agent_id": "agent-fraud-runtime",
                "application_id": app_id,
                "model_version": "gemini-1.5-pro",
                "langgraph_graph_version": "1.0.0",
                "context_source": "fresh",
                "context_token_count": 100,
                "started_at": datetime.now().isoformat(),
            },
        },
    )

    result = await run_fraud_detection_agent(
        store=store,
        registry=FakeRegistry(),
        application_id=app_id,
        agent_id="agent-fraud-runtime",
        model="gemini-1.5-pro",
        client=None,
        session_id=session_id,
        context_source="prior_session_replay:sess-fraud-runtime",
    )

    session_events = await store.load_stream(f"agent-fraud_detection-{session_id}")
    started = [event for event in session_events if event["event_type"] == "AgentSessionStarted"]
    assert result["session_id"] == session_id
    assert len(started) == 1
    assert any(event["event_type"] == "AgentSessionCompleted" for event in session_events)


@pytest.mark.asyncio
async def test_runtime_compliance_runner_reuses_existing_session_stream():
    store = InMemoryEventStore()
    app_id = "APEX-RUNTIME-003"
    session_id = "sess-compliance-runtime"
    await _seed_compliance_ready_application(store, app_id)

    await _append_event(
        store,
        f"agent-compliance-{session_id}",
        {
            "event_type": "AgentSessionStarted",
            "event_version": 1,
            "payload": {
                "session_id": session_id,
                "agent_type": "compliance",
                "agent_id": "agent-compliance-runtime",
                "application_id": app_id,
                "model_version": "gemini-1.5-pro",
                "langgraph_graph_version": "1.0.0",
                "context_source": "fresh",
                "context_token_count": 100,
                "started_at": datetime.now().isoformat(),
            },
        },
    )

    result = await run_compliance_agent(
        store=store,
        registry=FakeRegistry(),
        application_id=app_id,
        agent_id="agent-compliance-runtime",
        model="gemini-1.5-pro",
        client=None,
        session_id=session_id,
        context_source="prior_session_replay:sess-compliance-runtime",
    )

    session_events = await store.load_stream(f"agent-compliance-{session_id}")
    started = [event for event in session_events if event["event_type"] == "AgentSessionStarted"]
    assert result["session_id"] == session_id
    assert len(started) == 1
    assert any(event["event_type"] == "AgentSessionCompleted" for event in session_events)
