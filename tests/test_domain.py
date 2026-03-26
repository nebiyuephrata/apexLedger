"""
tests/test_domain.py
====================
Phase 2 domain logic tests (aggregates + command handlers).
These encode the non-negotiable business invariants.
"""
from __future__ import annotations
import pytest
from datetime import datetime
from uuid import uuid4

from ledger.domain.aggregates.loan_application import LoanApplicationAggregate, ApplicationState
from ledger.domain.aggregates.agent_session import AgentSessionAggregate
from ledger.domain.aggregates.compliance_record import ComplianceRecordAggregate
from ledger.domain.aggregates.audit_ledger import AuditLedgerAggregate
from ledger.domain.errors import DomainError
from ledger.commands.handlers import (
    handle_credit_analysis_completed,
    handle_decision_generated,
    handle_application_approved,
)
from ledger.event_store import OptimisticConcurrencyError


class InMemoryStore:
    def __init__(self, streams: dict[str, list[dict]] | None = None):
        self.streams: dict[str, list[dict]] = {}
        for sid, events in (streams or {}).items():
            self.streams[sid] = []
            pos = 0
            for e in events:
                pos += 1
                e.setdefault("event_version", 1)
                e.setdefault("payload", {})
                e["stream_position"] = pos
                self.streams[sid].append(e)

    async def load_stream(self, stream_id: str, from_position: int = 0, to_position: int | None = None) -> list[dict]:
        events = list(self.streams.get(stream_id, []))
        if to_position is not None:
            events = [e for e in events if from_position <= e["stream_position"] <= to_position]
        else:
            events = [e for e in events if e["stream_position"] >= from_position]
        return events

    async def stream_version(self, stream_id: str) -> int:
        events = self.streams.get(stream_id, [])
        return events[-1]["stream_position"] if events else -1

    async def append(self, stream_id: str, events: list[dict], expected_version: int,
                     causation_id: str | None = None, metadata: dict | None = None) -> list[int]:
        current = await self.stream_version(stream_id)
        if current != expected_version:
            raise OptimisticConcurrencyError(stream_id, expected_version, current)
        if stream_id not in self.streams:
            self.streams[stream_id] = []
        base = 1 if current == -1 else current + 1
        positions: list[int] = []
        for i, e in enumerate(events):
            pos = base + i
            e = dict(e)
            e.setdefault("event_version", 1)
            e.setdefault("payload", {})
            e["stream_position"] = pos
            self.streams[stream_id].append(e)
            positions.append(pos)
        return positions


def _app_submitted(app_id: str):
    return {
        "event_type": "ApplicationSubmitted",
        "event_version": 1,
        "payload": {
            "application_id": app_id,
            "applicant_id": "COMP-001",
            "requested_amount_usd": "100000",
            "loan_purpose": "working_capital",
            "loan_term_months": 24,
            "submission_channel": "web",
            "contact_email": "test@example.com",
            "contact_name": "Test User",
            "submitted_at": datetime.now().isoformat(),
            "application_reference": app_id,
        },
    }


def _decision_ready_events(app_id: str) -> list[dict]:
    return [
        _app_submitted(app_id),
        {"event_type": "DocumentUploadRequested", "payload": {"application_id": app_id}},
        {"event_type": "DocumentUploaded", "payload": {"application_id": app_id, "document_id": "doc-1"}},
        {"event_type": "CreditAnalysisRequested", "payload": {"application_id": app_id}},
        {"event_type": "FraudScreeningRequested", "payload": {"application_id": app_id}},
        {"event_type": "ComplianceCheckRequested", "payload": {"application_id": app_id}},
        {"event_type": "DecisionRequested", "payload": {"application_id": app_id}},
    ]


@pytest.mark.asyncio
async def test_state_reconstruction_load():
    app_id = f"APEX-{uuid4().hex[:6]}"
    store = InMemoryStore({
        f"loan-{app_id}": [
            _app_submitted(app_id),
            {"event_type": "DocumentUploadRequested", "payload": {"application_id": app_id}},
            {"event_type": "DocumentUploaded", "payload": {"application_id": app_id, "document_id": "doc-1"}},
            {"event_type": "CreditAnalysisRequested", "payload": {"application_id": app_id}},
            {"event_type": "FraudScreeningRequested", "payload": {"application_id": app_id}},
            {"event_type": "ComplianceCheckRequested", "payload": {"application_id": app_id}},
            {"event_type": "DecisionRequested", "payload": {"application_id": app_id}},
            {"event_type": "ApplicationApproved", "payload": {"application_id": app_id, "approved_amount_usd": "90000"}},
        ]
    })
    agg = await LoanApplicationAggregate.load(store, app_id)
    assert agg.application_id == app_id
    assert agg.state == ApplicationState.APPROVED
    assert agg.applicant_id == "COMP-001"
    assert agg.requested_amount_usd == 100000.0
    assert agg.canonical_state == "FinalApproved"


@pytest.mark.asyncio
async def test_compliance_record_reconstruction_load():
    app_id = f"APEX-{uuid4().hex[:6]}"
    store = InMemoryStore({
        f"compliance-{app_id}": [
            {
                "event_type": "ComplianceCheckInitiated",
                "payload": {
                    "application_id": app_id,
                    "session_id": "sess-comp",
                    "regulation_set_version": "2026-Q1",
                    "rules_to_evaluate": ["REG-001", "REG-002", "REG-003"],
                    "initiated_at": datetime.now().isoformat(),
                },
            },
            {
                "event_type": "ComplianceRulePassed",
                "payload": {
                    "application_id": app_id,
                    "session_id": "sess-comp",
                    "rule_id": "REG-001",
                    "rule_name": "BSA",
                    "rule_version": "2026-Q1-v1",
                    "evidence_hash": "h1",
                    "evaluation_notes": "ok",
                    "evaluated_at": datetime.now().isoformat(),
                },
            },
            {
                "event_type": "ComplianceRuleFailed",
                "payload": {
                    "application_id": app_id,
                    "session_id": "sess-comp",
                    "rule_id": "REG-003",
                    "rule_name": "Jurisdiction Eligibility",
                    "rule_version": "2026-Q1-v1",
                    "failure_reason": "MT",
                    "is_hard_block": True,
                    "remediation_available": False,
                    "evidence_hash": "h2",
                    "evaluated_at": datetime.now().isoformat(),
                },
            },
            {
                "event_type": "ComplianceCheckCompleted",
                "payload": {
                    "application_id": app_id,
                    "session_id": "sess-comp",
                    "rules_evaluated": 3,
                    "rules_passed": 1,
                    "rules_failed": 1,
                    "rules_noted": 0,
                    "has_hard_block": True,
                    "overall_verdict": "BLOCKED",
                    "completed_at": datetime.now().isoformat(),
                },
            },
        ]
    })
    agg = await ComplianceRecordAggregate.load(store, app_id)
    assert agg.application_id == app_id
    assert agg.regulation_set_version == "2026-Q1"
    assert "REG-001" in agg.passed_rules
    assert agg.failed_rules["REG-003"]["failure_reason"] == "MT"
    assert agg.has_hard_block is True
    assert agg.overall_verdict == "BLOCKED"


@pytest.mark.asyncio
async def test_audit_ledger_reconstruction_load():
    entity_id = f"APP-{uuid4().hex[:6]}"
    store = InMemoryStore({
        f"audit-{entity_id}": [
            {
                "event_type": "AuditIntegrityCheckRun",
                "payload": {
                    "entity_type": "application",
                    "entity_id": entity_id,
                    "check_timestamp": datetime.now().isoformat(),
                    "events_verified_count": 12,
                    "integrity_hash": "hash-2",
                    "previous_hash": "hash-1",
                    "chain_valid": True,
                    "tamper_detected": False,
                },
            },
        ]
    })
    agg = await AuditLedgerAggregate.load(store, entity_id)
    assert agg.entity_id == entity_id
    assert agg.entity_type == "application"
    assert agg.checks_run == 1
    assert agg.last_integrity_hash == "hash-2"
    assert agg.previous_hash == "hash-1"


@pytest.mark.asyncio
async def test_multiple_document_uploads_replay_in_documents_uploaded_state():
    app_id = f"APEX-{uuid4().hex[:6]}"
    store = InMemoryStore({
        f"loan-{app_id}": [
            _app_submitted(app_id),
            {"event_type": "DocumentUploadRequested", "payload": {"application_id": app_id}},
            {"event_type": "DocumentUploaded", "payload": {"application_id": app_id, "document_id": "doc-1", "document_type": "application_proposal"}},
            {"event_type": "DocumentUploaded", "payload": {"application_id": app_id, "document_id": "doc-2", "document_type": "income_statement"}},
            {"event_type": "DocumentUploaded", "payload": {"application_id": app_id, "document_id": "doc-3", "document_type": "balance_sheet"}},
        ]
    })
    agg = await LoanApplicationAggregate.load(store, app_id)
    assert agg.state == ApplicationState.DOCUMENTS_UPLOADED
    assert set(agg.documents) == {"doc-1", "doc-2", "doc-3"}


def test_state_machine_invalid_transition_raises():
    agg = LoanApplicationAggregate(application_id="APEX-INV")
    agg.state = ApplicationState.APPROVED
    with pytest.raises(DomainError):
        agg.assert_valid_transition(ApplicationState.PENDING_DECISION)


@pytest.mark.asyncio
async def test_agent_session_requires_gas_town_anchor():
    stream_id = "agent-credit_analysis-sess-1"
    store = InMemoryStore({
        stream_id: [
            {"event_type": "AgentNodeExecuted", "payload": {"session_id": "sess-1", "agent_type": "credit_analysis"}},
        ]
    })
    with pytest.raises(DomainError):
        await AgentSessionAggregate.load(store, stream_id)


@pytest.mark.asyncio
async def test_model_locking_duplicate_credit_analysis_rejected():
    app_id = f"APEX-{uuid4().hex[:6]}"
    store = InMemoryStore({
        f"loan-{app_id}": [
            _app_submitted(app_id),
            {"event_type": "DocumentUploadRequested", "payload": {"application_id": app_id}},
            {"event_type": "DocumentUploaded", "payload": {"application_id": app_id, "document_id": "doc-1"}},
            {"event_type": "CreditAnalysisRequested", "payload": {"application_id": app_id}},
        ],
        f"credit-{app_id}": [
            {"event_type": "CreditAnalysisCompleted", "event_version": 2, "payload": {
                "application_id": app_id, "session_id": "sess-old", "decision": {
                    "risk_tier": "LOW", "recommended_limit_usd": "50000", "confidence": 0.85,
                    "rationale": "ok",
                }, "model_version": "m1", "model_deployment_id": "d1",
                "input_data_hash": "h1", "analysis_duration_ms": 10, "completed_at": datetime.now().isoformat(),
            }},
        ],
        "agent-credit_analysis-sess-new": [
            {"event_type": "AgentSessionStarted", "payload": {
                "session_id": "sess-new", "agent_type": "credit_analysis", "agent_id": "agent-1",
                "application_id": app_id, "model_version": "m2", "langgraph_graph_version": "1.0.0",
                "context_source": "fresh", "context_token_count": 100, "started_at": datetime.now().isoformat(),
            }},
        ],
    })

    with pytest.raises(DomainError):
        await handle_credit_analysis_completed(
            store,
            {
                "application_id": app_id,
                "session_id": "sess-new",
                "agent_type": "credit_analysis",
                "decision": {
                    "risk_tier": "MEDIUM",
                    "recommended_limit_usd": 60000,
                    "confidence": 0.7,
                    "rationale": "ok",
                },
                "model_version": "m2",
                "analysis_duration_ms": 5,
            },
        )


@pytest.mark.asyncio
async def test_confidence_floor_forces_refer():
    """
    If confidence < 0.60, recommendation must be REFER regardless of LLM suggestion.
    This test is expected to fail until decision handling enforces the rule.
    """
    app_id = f"APEX-{uuid4().hex[:6]}"
    store = InMemoryStore({
        f"loan-{app_id}": [
            *_decision_ready_events(app_id),
            {"event_type": "DecisionGenerated", "event_version": 2, "payload": {
                "application_id": app_id,
                "orchestrator_session_id": "sess-orch",
                "recommendation": "APPROVE",
                "confidence": 0.55,
                "executive_summary": "low confidence",
                "generated_at": datetime.now().isoformat(),
            }},
        ]
    })
    agg = await LoanApplicationAggregate.load(store, app_id)
    assert agg.decision_recommendation == "REFER"
    assert agg.state == ApplicationState.PENDING_HUMAN_REVIEW
    assert agg.canonical_state == "DeclinedPendingHuman"


@pytest.mark.asyncio
async def test_decision_generated_sets_pending_human_states():
    app_id = f"APEX-{uuid4().hex[:6]}"
    approve_store = InMemoryStore({
        f"loan-{app_id}": [
            *_decision_ready_events(app_id),
            {"event_type": "DecisionGenerated", "event_version": 2, "payload": {
                "application_id": app_id,
                "orchestrator_session_id": "sess-orch",
                "recommendation": "APPROVE",
                "confidence": 0.88,
                "executive_summary": "approve",
                "generated_at": datetime.now().isoformat(),
            }},
        ]
    })
    approve_agg = await LoanApplicationAggregate.load(approve_store, app_id)
    assert approve_agg.state == ApplicationState.APPROVED_PENDING_HUMAN
    assert approve_agg.canonical_state == "ApprovedPendingHuman"

    decline_store = InMemoryStore({
        f"loan-{app_id}": [
            *_decision_ready_events(app_id),
            {"event_type": "DecisionGenerated", "event_version": 2, "payload": {
                "application_id": app_id,
                "orchestrator_session_id": "sess-orch",
                "recommendation": "DECLINE",
                "confidence": 0.88,
                "executive_summary": "decline",
                "generated_at": datetime.now().isoformat(),
            }},
        ]
    })
    decline_agg = await LoanApplicationAggregate.load(decline_store, app_id)
    assert decline_agg.state == ApplicationState.DECLINED_PENDING_HUMAN
    assert decline_agg.canonical_state == "DeclinedPendingHuman"


@pytest.mark.asyncio
async def test_compliance_dependency_blocks_approval():
    """
    ApplicationApproved cannot be appended unless mandatory ComplianceRulePassed events exist.
    This test is expected to fail until approval command handler enforces compliance dependency.
    """
    app_id = f"APEX-{uuid4().hex[:6]}"
    store = InMemoryStore({
        f"loan-{app_id}": [
            _app_submitted(app_id),
            {"event_type": "DecisionRequested", "payload": {"application_id": app_id}},
        ],
        f"compliance-{app_id}": [
            {"event_type": "ComplianceCheckInitiated", "payload": {
                "application_id": app_id, "session_id": "sess-comp", "regulation_set_version": "2026-Q1",
                "rules_to_evaluate": ["REG-001", "REG-002", "REG-003"],
                "initiated_at": datetime.now().isoformat(),
            }},
            {"event_type": "ComplianceRulePassed", "payload": {
                "application_id": app_id, "session_id": "sess-comp", "rule_id": "REG-001",
                "rule_name": "BSA", "rule_version": "2026-Q1-v1", "evidence_hash": "h",
                "evaluation_notes": "ok", "evaluated_at": datetime.now().isoformat(),
            }},
        ],
    })
    # Invariant: without all required ComplianceRulePassed, approval should be blocked.
    with pytest.raises(DomainError):
        await handle_application_approved(
            store,
            {
                "application_id": app_id,
                "approved_amount_usd": 75000,
                "interest_rate_pct": 6.5,
                "term_months": 36,
                "conditions": [],
                "approved_by": "auto",
            },
        )


@pytest.mark.asyncio
async def test_causal_chain_verification_for_decision_sessions():
    """
    DecisionGenerated must reference AgentSession IDs that processed the same application.
    This test is expected to fail until decision handling validates session streams.
    """
    app_id = f"APEX-{uuid4().hex[:6]}"
    store = InMemoryStore({
        f"loan-{app_id}": [
            _app_submitted(app_id),
            {"event_type": "DecisionRequested", "payload": {"application_id": app_id}},
        ],
        # session streams for different application should not satisfy causal chain
        "agent-credit_analysis-sess-a": [
            {"event_type": "AgentSessionStarted", "payload": {
                "session_id": "sess-a", "agent_type": "credit_analysis", "agent_id": "agent-a",
                "application_id": "OTHER-APP", "model_version": "m", "langgraph_graph_version": "1.0.0",
                "context_source": "fresh", "context_token_count": 100, "started_at": datetime.now().isoformat(),
            }},
        ],
    })
    # Expected behavior: reject because contributing sessions are not tied to this application.
    with pytest.raises(DomainError):
        await handle_decision_generated(
            store,
            {
                "application_id": app_id,
                "orchestrator_session_id": "sess-orch",
                "recommendation": "APPROVE",
                "confidence": 0.9,
                "executive_summary": "ok",
                "contributing_sessions": ["sess-a", "sess-b"],
            },
        )
