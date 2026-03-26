from __future__ import annotations

from datetime import datetime

import pytest

from ledger.agents.decision_orchestrator_agent import DecisionOrchestratorAgent
from ledger.agents.runtime import run_decision_orchestrator_agent
from ledger.event_store import InMemoryEventStore
from ledger.registry.client import CompanyProfile
from ledger.schema.events import (
    ApplicationSubmitted,
    ComplianceCheckCompleted,
    ComplianceCheckInitiated,
    ComplianceRulePassed,
    ComplianceVerdict,
    CreditAnalysisCompleted,
    CreditAnalysisRequested,
    CreditDecision,
    DecisionRequested,
    DocumentType,
    DocumentUploadRequested,
    DocumentUploaded,
    FraudScreeningCompleted,
    FraudScreeningRequested,
    RiskTier,
)


class FakeRegistry:
    async def get_company(self, company_id: str) -> CompanyProfile | None:
        return CompanyProfile(
            company_id=company_id,
            name="Orchestrator Holdings",
            industry="services",
            naics="541611",
            jurisdiction="CA",
            legal_type="LLC",
            founded_year=2015,
            employee_count=30,
            risk_segment="SMB",
            trajectory="STABLE",
            submission_channel="direct",
            ip_region="us-west",
        )

    async def get_financial_history(self, company_id: str, years: list[int] | None = None) -> list[dict]:
        return []

    async def get_compliance_flags(self, company_id: str, active_only: bool = False) -> list[dict]:
        return []

    async def get_loan_relationships(self, company_id: str) -> list[dict]:
        return []


class FakeMessagesAPI:
    def __init__(self, response_text: str):
        self.response_text = response_text

    async def create(self, **_: object):
        text = self.response_text

        class FakeResponse:
            class Usage:
                input_tokens = 120
                output_tokens = 80

            class Block:
                text = ""

            content = [Block()]
            usage = Usage()

        FakeResponse.content[0].text = text
        return FakeResponse()


class FakeLLMClient:
    def __init__(self, response_text: str):
        self.messages = FakeMessagesAPI(response_text)


async def _append_event(store: InMemoryEventStore, stream_id: str, event: dict) -> None:
    version = await store.stream_version(stream_id)
    await store.append(stream_id=stream_id, events=[event], expected_version=version)


async def _seed_supporting_session(store: InMemoryEventStore, stream_id: str, session_id: str, agent_type: str, app_id: str, model_version: str) -> None:
    await _append_event(
        store,
        stream_id,
        {
            "event_type": "AgentSessionStarted",
            "event_version": 1,
            "payload": {
                "session_id": session_id,
                "agent_type": agent_type,
                "agent_id": f"agent-{agent_type}",
                "application_id": app_id,
                "model_version": model_version,
                "langgraph_graph_version": "1.0.0",
                "context_source": "fresh",
                "context_token_count": 100,
                "started_at": datetime.now().isoformat(),
            },
        },
    )


async def _seed_orchestrator_ready_application(store: InMemoryEventStore, app_id: str, fraud_score: float = 0.12) -> None:
    await _append_event(
        store,
        f"loan-{app_id}",
        ApplicationSubmitted(
            application_id=app_id,
            applicant_id="COMP-ORCH-1",
            requested_amount_usd="275000",
            loan_purpose="working_capital",
            loan_term_months=18,
            submission_channel="web",
            contact_email="orch@test.local",
            contact_name="Olivia Orchestrator",
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
            document_id="doc-orch-income",
            document_type=DocumentType.INCOME_STATEMENT,
            document_format="pdf",
            filename="income.pdf",
            file_path="/tmp/income.pdf",
            file_size_bytes=1024,
            file_hash="hash-orch-income",
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
            triggered_by_event_id="sess-credit-orch",
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        {
            "event_type": "ComplianceCheckRequested",
            "event_version": 1,
            "payload": {
                "application_id": app_id,
                "requested_at": datetime.now().isoformat(),
                "triggered_by_event_id": "sess-fraud-orch",
                "regulation_set_version": "2026-Q1",
                "rules_to_evaluate": ["REG-001", "REG-002", "REG-003"],
            },
        },
    )
    await _append_event(
        store,
        f"loan-{app_id}",
        DecisionRequested(
            application_id=app_id,
            requested_at=datetime.now(),
            all_analyses_complete=True,
            triggered_by_event_id="sess-comp-orch",
        ).to_store_dict(),
    )

    await _append_event(
        store,
        f"credit-{app_id}",
        CreditAnalysisCompleted(
            application_id=app_id,
            session_id="sess-credit-orch",
            decision=CreditDecision(
                risk_tier=RiskTier.LOW,
                recommended_limit_usd="200000",
                confidence=0.82,
                rationale="Credit is acceptable.",
            ),
            model_version="gemini-credit",
            model_deployment_id="dep-credit",
            input_data_hash="credit-hash",
            analysis_duration_ms=30,
            completed_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"fraud-{app_id}",
        FraudScreeningCompleted(
            application_id=app_id,
            session_id="sess-fraud-orch",
            fraud_score=fraud_score,
            risk_level="HIGH" if fraud_score >= 0.75 else "LOW",
            anomalies_found=1 if fraud_score >= 0.75 else 0,
            recommendation="ESCALATE" if fraud_score >= 0.75 else "CLEAR",
            screening_model_version="gemini-fraud",
            input_data_hash="fraud-hash",
            completed_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"compliance-{app_id}",
        ComplianceCheckInitiated(
            application_id=app_id,
            session_id="sess-comp-orch",
            regulation_set_version="2026-Q1",
            rules_to_evaluate=["REG-001", "REG-002", "REG-003"],
            initiated_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"compliance-{app_id}",
        ComplianceRulePassed(
            application_id=app_id,
            session_id="sess-comp-orch",
            rule_id="REG-001",
            rule_name="BSA",
            rule_version="2026-Q1-v1",
            evidence_hash="hash-1",
            evaluation_notes="passed",
            evaluated_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"compliance-{app_id}",
        ComplianceRulePassed(
            application_id=app_id,
            session_id="sess-comp-orch",
            rule_id="REG-002",
            rule_name="OFAC",
            rule_version="2026-Q1-v1",
            evidence_hash="hash-2",
            evaluation_notes="passed",
            evaluated_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"compliance-{app_id}",
        ComplianceRulePassed(
            application_id=app_id,
            session_id="sess-comp-orch",
            rule_id="REG-003",
            rule_name="Jurisdiction",
            rule_version="2026-Q1-v1",
            evidence_hash="hash-3",
            evaluation_notes="passed",
            evaluated_at=datetime.now(),
        ).to_store_dict(),
    )
    await _append_event(
        store,
        f"compliance-{app_id}",
        ComplianceCheckCompleted(
            application_id=app_id,
            session_id="sess-comp-orch",
            rules_evaluated=3,
            rules_passed=3,
            rules_failed=0,
            rules_noted=0,
            has_hard_block=False,
            overall_verdict=ComplianceVerdict.CLEAR,
            completed_at=datetime.now(),
        ).to_store_dict(),
    )

    await _seed_supporting_session(store, "agent-credit_analysis-sess-credit-orch", "sess-credit-orch", "credit_analysis", app_id, "gemini-credit")
    await _seed_supporting_session(store, "agent-fraud_detection-sess-fraud-orch", "sess-fraud-orch", "fraud_detection", app_id, "gemini-fraud")
    await _seed_supporting_session(store, "agent-compliance-sess-comp-orch", "sess-comp-orch", "compliance", app_id, "2026-Q1")


@pytest.mark.asyncio
async def test_orchestrator_agent_generates_decision():
    store = InMemoryEventStore()
    app_id = "APEX-ORCH-001"
    await _seed_orchestrator_ready_application(store, app_id, fraud_score=0.12)

    client = FakeLLMClient(
        """
        {
          "recommendation": "APPROVE",
          "confidence": 0.88,
          "approved_amount_usd": 190000,
          "conditions": ["Standard reporting covenant."],
          "executive_summary": "Approve based on aligned credit, fraud, and compliance signals.",
          "key_risks": ["Moderate customer concentration"]
        }
        """
    )
    agent = DecisionOrchestratorAgent(
        agent_id="agent-orch-1",
        agent_type="decision_orchestrator",
        store=store,
        registry=FakeRegistry(),
        client=client,
        model="gemini-orchestrator",
    )

    await agent.process_application(app_id)

    loan_events = await store.load_stream(f"loan-{app_id}")
    session_events = await store.load_stream(agent._session_stream)
    decisions = [event for event in loan_events if event["event_type"] == "DecisionGenerated"]
    approvals = [event for event in loan_events if event["event_type"] == "ApplicationApproved"]
    assert len(decisions) == 1
    assert decisions[0]["payload"]["recommendation"] == "APPROVE"
    assert decisions[0]["payload"]["contributing_sessions"] == ["sess-credit-orch", "sess-fraud-orch", "sess-comp-orch"]
    assert len(approvals) == 1
    assert not any(event["event_type"] == "HumanReviewRequested" for event in loan_events)
    assert session_events[0]["event_type"] == "AgentSessionStarted"
    assert session_events[-1]["event_type"] == "AgentSessionCompleted"


@pytest.mark.asyncio
async def test_orchestrator_agent_forces_refer_and_requests_human_review():
    store = InMemoryEventStore()
    app_id = "APEX-ORCH-002"
    await _seed_orchestrator_ready_application(store, app_id, fraud_score=0.84)

    result = await run_decision_orchestrator_agent(
        store=store,
        registry=FakeRegistry(),
        application_id=app_id,
        agent_id="agent-orch-2",
        model="gemini-orchestrator",
        client=FakeLLMClient(
            """
            {
              "recommendation": "APPROVE",
              "confidence": 0.87,
              "approved_amount_usd": 180000,
              "conditions": [],
              "executive_summary": "Initial synthesis would approve.",
              "key_risks": []
            }
            """
        ),
        session_id="sess-orch-runtime",
        context_source="fresh",
    )

    loan_events = await store.load_stream(f"loan-{app_id}")
    decisions = [event for event in loan_events if event["event_type"] == "DecisionGenerated"]
    review_requests = [event for event in loan_events if event["event_type"] == "HumanReviewRequested"]
    assert result["session_id"] == "sess-orch-runtime"
    assert len(decisions) == 1
    assert decisions[0]["payload"]["recommendation"] == "REFER"
    assert len(review_requests) == 1


@pytest.mark.asyncio
async def test_orchestrator_agent_records_decline_terminal_event():
    store = InMemoryEventStore()
    app_id = "APEX-ORCH-003"
    await _seed_orchestrator_ready_application(store, app_id, fraud_score=0.12)

    agent = DecisionOrchestratorAgent(
        agent_id="agent-orch-3",
        agent_type="decision_orchestrator",
        store=store,
        registry=FakeRegistry(),
        client=FakeLLMClient(
            """
            {
              "recommendation": "DECLINE",
              "confidence": 0.91,
              "approved_amount_usd": null,
              "conditions": [],
              "executive_summary": "Decline due to concentrated downside risk.",
              "key_risks": ["Concentrated downside risk"]
            }
            """
        ),
        model="gemini-orchestrator",
    )

    await agent.process_application(app_id)

    loan_events = await store.load_stream(f"loan-{app_id}")
    decisions = [event for event in loan_events if event["event_type"] == "DecisionGenerated"]
    declines = [event for event in loan_events if event["event_type"] == "ApplicationDeclined"]
    assert len(decisions) == 1
    assert decisions[0]["payload"]["recommendation"] == "DECLINE"
    assert len(declines) == 1
