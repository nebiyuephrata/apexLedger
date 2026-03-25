from __future__ import annotations

from datetime import datetime

import pytest

from ledger.agents.credit_analysis_agent import CreditAnalysisAgent
from ledger.event_store import InMemoryEventStore
from ledger.registry.client import CompanyProfile, ComplianceFlag, FinancialYear
from ledger.schema.events import (
    ApplicationSubmitted,
    CreditAnalysisRequested,
    DocumentAdded,
    DocumentUploadRequested,
    DocumentType,
    DocumentUploaded,
    ExtractionCompleted,
    FinancialFacts,
    PackageCreated,
)


class FakeRegistry:
    async def get_company(self, company_id: str) -> CompanyProfile | None:
        return CompanyProfile(
            company_id=company_id,
            name="Apex Components",
            industry="manufacturing",
            naics="336390",
            jurisdiction="CA",
            legal_type="LLC",
            founded_year=2013,
            employee_count=128,
            risk_segment="MIDDLE_MARKET",
            trajectory="STABLE",
            submission_channel="broker",
            ip_region="us-west",
        )

    async def get_financial_history(self, company_id: str, years: list[int] | None = None) -> list[FinancialYear]:
        return [
            FinancialYear(
                fiscal_year=2023,
                total_revenue=1_000_000.0,
                gross_profit=420_000.0,
                operating_income=170_000.0,
                ebitda=200_000.0,
                net_income=120_000.0,
                total_assets=900_000.0,
                total_liabilities=450_000.0,
                total_equity=450_000.0,
                long_term_debt=150_000.0,
                cash_and_equivalents=110_000.0,
                current_assets=310_000.0,
                current_liabilities=140_000.0,
                accounts_receivable=90_000.0,
                inventory=60_000.0,
                debt_to_equity=1.0,
                current_ratio=2.2,
                debt_to_ebitda=0.75,
                interest_coverage_ratio=4.1,
                gross_margin=0.42,
                ebitda_margin=0.20,
                net_margin=0.12,
            ),
            FinancialYear(
                fiscal_year=2024,
                total_revenue=1_200_000.0,
                gross_profit=480_000.0,
                operating_income=190_000.0,
                ebitda=230_000.0,
                net_income=140_000.0,
                total_assets=1_000_000.0,
                total_liabilities=470_000.0,
                total_equity=530_000.0,
                long_term_debt=175_000.0,
                cash_and_equivalents=125_000.0,
                current_assets=340_000.0,
                current_liabilities=150_000.0,
                accounts_receivable=100_000.0,
                inventory=65_000.0,
                debt_to_equity=0.89,
                current_ratio=2.27,
                debt_to_ebitda=0.76,
                interest_coverage_ratio=4.3,
                gross_margin=0.40,
                ebitda_margin=0.19,
                net_margin=0.12,
            ),
        ]

    async def get_compliance_flags(self, company_id: str, active_only: bool = False) -> list[ComplianceFlag]:
        flags = [
            ComplianceFlag(
                flag_type="AML_REVIEW",
                severity="HIGH",
                is_active=True,
                added_date="2026-01-10",
                note="Enhanced due diligence required.",
            )
        ]
        if active_only:
            return [flag for flag in flags if flag.is_active]
        return flags

    async def get_loan_relationships(self, company_id: str) -> list[dict]:
        return [
            {
                "loan_amount": 180_000,
                "loan_year": 2022,
                "was_repaid": False,
                "default_occurred": True,
                "note": "Prior default on revolving line.",
            }
        ]


class FakeMessagesAPI:
    async def create(self, **_: object):
        class FakeResponse:
            class Usage:
                input_tokens = 180
                output_tokens = 90

            class Block:
                text = """
                {
                  "risk_tier": "LOW",
                  "recommended_limit_usd": 600000,
                  "confidence": 0.86,
                  "rationale": "Positive operating trend with acceptable leverage.",
                  "key_concerns": ["Customer concentration"],
                  "data_quality_caveats": ["Inventory aging detail not available"],
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


async def _seed_valid_application(store: InMemoryEventStore, app_id: str) -> None:
    await _append_event(
        store,
        f"loan-{app_id}",
        ApplicationSubmitted(
            application_id=app_id,
            applicant_id="COMP-001",
            requested_amount_usd="500000",
            loan_purpose="working_capital",
            loan_term_months=24,
            submission_channel="web",
            contact_email="ops@apex.test",
            contact_name="Ada Operator",
            submitted_at=datetime.now(),
            application_reference=app_id,
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        DocumentUploadRequested(
            application_id=app_id,
            required_document_types=[DocumentType.INCOME_STATEMENT, DocumentType.BALANCE_SHEET],
            deadline=datetime.now(),
            requested_by="system",
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        DocumentUploaded(
            application_id=app_id,
            document_id="doc-income-1",
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
        f"docpkg-{app_id}",
        PackageCreated(
            package_id=app_id,
            application_id=app_id,
            required_documents=[DocumentType.INCOME_STATEMENT, DocumentType.BALANCE_SHEET],
            created_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"docpkg-{app_id}",
        DocumentAdded(
            package_id=app_id,
            document_id="doc-income-1",
            document_type=DocumentType.INCOME_STATEMENT,
            document_format="pdf",
            file_hash="hash-income",
            added_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"docpkg-{app_id}",
        ExtractionCompleted(
            package_id=app_id,
            document_id="doc-income-1",
            document_type=DocumentType.INCOME_STATEMENT,
            facts=FinancialFacts(
                total_revenue="1200000",
                gross_profit="480000",
                ebitda="230000",
                net_income="140000",
                extraction_notes=["Revenue validated from statement"],
            ),
            raw_text_length=5000,
            tables_extracted=3,
            processing_ms=320,
            completed_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"docpkg-{app_id}",
        ExtractionCompleted(
            package_id=app_id,
            document_id="doc-bs-1",
            document_type=DocumentType.BALANCE_SHEET,
            facts=FinancialFacts(
                total_assets="1000000",
                total_liabilities="470000",
                total_equity="530000",
                current_assets="340000",
                current_liabilities="150000",
                extraction_notes=["Balance sheet balanced"],
            ),
            raw_text_length=4000,
            tables_extracted=2,
            processing_ms=280,
            completed_at=datetime.now(),
        ).to_store_dict(),
    )


@pytest.mark.asyncio
async def test_credit_agent_processes_application_and_triggers_fraud_screening():
    store = InMemoryEventStore()
    app_id = "APEX-CREDIT-001"
    await _seed_valid_application(store, app_id)

    agent = CreditAnalysisAgent(
        agent_id="credit-agent-1",
        agent_type="credit_analysis",
        store=store,
        registry=FakeRegistry(),
        client=FakeLLMClient(),
        model="gemini-1.5-pro",
    )

    await agent.process_application(app_id)

    credit_events = await store.load_stream(f"credit-{app_id}")
    loan_events = await store.load_stream(f"loan-{app_id}")
    session_events = await store.load_stream(agent._session_stream)

    assert [event["event_type"] for event in credit_events] == [
        "CreditRecordOpened",
        "HistoricalProfileConsumed",
        "ExtractedFactsConsumed",
        "CreditAnalysisCompleted",
    ]
    completed = credit_events[-1]["payload"]["decision"]
    assert completed["risk_tier"] == "HIGH"
    assert completed["recommended_limit_usd"] == "420000"
    assert completed["confidence"] == 0.5
    assert any("POLICY_PRIOR_DEFAULT" in item for item in completed["policy_overrides_applied"])
    assert any("POLICY_COMPLIANCE_FLAG" in item for item in completed["policy_overrides_applied"])
    assert any(event["event_type"] == "FraudScreeningRequested" for event in loan_events)
    assert session_events[0]["event_type"] == "AgentSessionStarted"
    assert session_events[-1]["event_type"] == "AgentSessionCompleted"


@pytest.mark.asyncio
async def test_credit_agent_rejects_application_without_extracted_facts():
    store = InMemoryEventStore()
    app_id = "APEX-CREDIT-002"
    await _append_event(
        store,
        f"loan-{app_id}",
        ApplicationSubmitted(
            application_id=app_id,
            applicant_id="COMP-002",
            requested_amount_usd="250000",
            loan_purpose="working_capital",
            loan_term_months=12,
            submission_channel="web",
            contact_email="ops@apex.test",
            contact_name="Ben Borrower",
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
            document_id="doc-income-2",
            document_type=DocumentType.INCOME_STATEMENT,
            document_format="pdf",
            filename="income.pdf",
            file_path="/tmp/income.pdf",
            file_size_bytes=1024,
            file_hash="hash-income-2",
            fiscal_year=2024,
            uploaded_at=datetime.now(),
            uploaded_by="user-2",
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

    agent = CreditAnalysisAgent(
        agent_id="credit-agent-2",
        agent_type="credit_analysis",
        store=store,
        registry=FakeRegistry(),
        client=FakeLLMClient(),
        model="gemini-1.5-pro",
    )

    with pytest.raises(ValueError, match="ExtractionCompleted facts"):
        await agent.process_application(app_id)

    credit_events = await store.load_stream(f"credit-{app_id}")
    session_events = await store.load_stream(agent._session_stream)
    assert credit_events == []
    assert any(event["event_type"] == "AgentInputValidationFailed" for event in session_events)
    assert session_events[-1]["event_type"] == "AgentSessionFailed"
