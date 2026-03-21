"""
ledger/commands/handlers.py
===========================
Command handlers implementing strict CQRS patterns.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ledger.domain.aggregates.loan_application import LoanApplicationAggregate, ApplicationState
from ledger.domain.aggregates.agent_session import AgentSessionAggregate
from ledger.domain.errors import DomainError
from ledger.schema.events import (
    ApplicationSubmitted, DocumentUploadRequested, PackageCreated,
    CreditAnalysisCompleted, CreditDecision, FraudScreeningRequested,
    DecisionGenerated, ApplicationApproved,
    ComplianceCheckInitiated, ComplianceRulePassed, ComplianceRuleFailed,
    DocumentType, LoanPurpose, RiskTier, AgentType,
)


def _get(cmd: Any, key: str, default: Any = None) -> Any:
    if isinstance(cmd, dict):
        return cmd.get(key, default)
    return getattr(cmd, key, default)


def _dget(decision: Any, key: str, default: Any = None) -> Any:
    if isinstance(decision, dict):
        return decision.get(key, default)
    return getattr(decision, key, default)


async def handle_submit_application(store, command: Any) -> list[dict]:
    """
    Create a new loan application and request document uploads.
    """
    app_id = _get(command, "application_id")
    if not app_id:
        raise DomainError("application_id is required")

    # 1) Reconstruct state
    app = await LoanApplicationAggregate.load(store, app_id)

    # 2) Validate business rules
    if app.version != -1 or app.state != ApplicationState.NEW:
        raise DomainError(f"Application {app_id} already exists")

    applicant_id = _get(command, "applicant_id")
    requested_amount_usd = _get(command, "requested_amount_usd")
    loan_purpose = _get(command, "loan_purpose")
    loan_term_months = _get(command, "loan_term_months", 36)
    submission_channel = _get(command, "submission_channel", "web")
    contact_email = _get(command, "contact_email", "unknown@example.com")
    contact_name = _get(command, "contact_name", "Unknown")
    submitted_at = _get(command, "submitted_at", datetime.now())
    application_reference = _get(command, "application_reference", app_id)

    if not applicant_id or requested_amount_usd is None or not loan_purpose:
        raise DomainError("applicant_id, requested_amount_usd, and loan_purpose are required")

    try:
        loan_purpose_enum = LoanPurpose(loan_purpose)
    except Exception:
        loan_purpose_enum = LoanPurpose.WORKING_CAPITAL

    required_docs_raw = _get(
        command,
        "required_document_types",
        [
            DocumentType.APPLICATION_PROPOSAL,
            DocumentType.INCOME_STATEMENT,
            DocumentType.BALANCE_SHEET,
        ],
    )
    required_docs: list[DocumentType] = []
    for d in required_docs_raw:
        try:
            required_docs.append(d if isinstance(d, DocumentType) else DocumentType(d))
        except Exception:
            continue
    deadline = _get(command, "deadline", submitted_at + timedelta(days=7))
    requested_by = _get(command, "requested_by", "system")

    # 3) Determine events (pure)
    submit_event = ApplicationSubmitted(
        application_id=app_id,
        applicant_id=applicant_id,
        requested_amount_usd=Decimal(str(requested_amount_usd)),
        loan_purpose=loan_purpose_enum,
        loan_term_months=int(loan_term_months),
        submission_channel=submission_channel,
        contact_email=contact_email,
        contact_name=contact_name,
        submitted_at=submitted_at,
        application_reference=application_reference,
    ).to_store_dict()

    doc_req_event = DocumentUploadRequested(
        application_id=app_id,
        required_document_types=required_docs,
        deadline=deadline,
        requested_by=requested_by,
    ).to_store_dict()

    package_event = PackageCreated(
        package_id=app_id,
        application_id=app_id,
        required_documents=required_docs,
        created_at=submitted_at,
    ).to_store_dict()

    # 4) Append atomically per stream with expected_version
    correlation_id = _get(command, "correlation_id")
    causation_id = _get(command, "causation_id")
    meta = {"correlation_id": correlation_id} if correlation_id else None
    await store.append(
        stream_id=f"loan-{app_id}",
        events=[submit_event, doc_req_event],
        expected_version=app.version,
        causation_id=causation_id,
        metadata=meta,
    )
    docpkg_version = await store.stream_version(f"docpkg-{app_id}")
    await store.append(
        stream_id=f"docpkg-{app_id}",
        events=[package_event],
        expected_version=docpkg_version,
        causation_id=causation_id,
        metadata=meta,
    )

    return [submit_event, doc_req_event, package_event]


async def handle_credit_analysis_completed(store, command: Any) -> list[dict]:
    """
    Record CreditAnalysisCompleted and trigger FraudScreeningRequested.
    Enforces: loan state, Gas Town anchor, model version locking.
    """
    app_id = _get(command, "application_id")
    session_id = _get(command, "session_id")
    agent_type = _get(command, "agent_type", "credit_analysis")
    if not app_id or not session_id:
        raise DomainError("application_id and session_id are required")

    # 1) Reconstruct state
    app = await LoanApplicationAggregate.load(store, app_id)
    session_stream = f"agent-{agent_type}-{session_id}"
    session = await AgentSessionAggregate.load(store, session_stream)

    # 2) Validate business rules
    if not session.started:
        raise DomainError("Agent session is missing AgentSessionStarted anchor")
    session.require_context_loaded()
    session.require_model_version(_get(command, "model_version"))
    if session.application_id and session.application_id != app_id:
        raise DomainError("Agent session application_id mismatch")

    if app.state not in (ApplicationState.DOCUMENTS_PROCESSED, ApplicationState.CREDIT_ANALYSIS_REQUESTED):
        raise DomainError(f"Application {app_id} not awaiting analysis (state={app.state})")

    credit_stream = f"credit-{app_id}"
    credit_events = await store.load_stream(credit_stream)
    has_analysis = any(e.get("event_type") == "CreditAnalysisCompleted" for e in credit_events)
    has_override = False
    for ev in app.events:
        if ev.get("event_type") == "HumanReviewCompleted":
            if ev.get("payload", {}).get("override"):
                has_override = True
                break
    if has_analysis and not has_override:
        raise DomainError("CreditAnalysisCompleted already exists without HumanReview override")

    decision = _get(command, "decision")
    if not decision:
        raise DomainError("decision is required")

    # 3) Determine events (pure)
    try:
        risk_tier = RiskTier(_dget(decision, "risk_tier", "MEDIUM"))
    except Exception as e:
        raise DomainError(f"Invalid risk_tier: {_dget(decision, 'risk_tier')}") from e

    decision_event = CreditAnalysisCompleted(
        application_id=app_id,
        session_id=session_id,
        decision=CreditDecision(
            risk_tier=risk_tier,
            recommended_limit_usd=Decimal(str(_dget(decision, "recommended_limit_usd", 0))),
            confidence=float(_dget(decision, "confidence", 0.0)),
            rationale=_dget(decision, "rationale", ""),
            key_concerns=_dget(decision, "key_concerns", []),
            data_quality_caveats=_dget(decision, "data_quality_caveats", []),
            policy_overrides_applied=_dget(decision, "policy_overrides_applied", []),
        ),
        model_version=_get(command, "model_version", "unknown"),
        model_deployment_id=_get(command, "model_deployment_id", f"dep-{session_id[:6]}"),
        input_data_hash=_get(command, "input_data_hash", "unknown"),
        analysis_duration_ms=int(_get(command, "analysis_duration_ms", 0)),
        regulatory_basis=_get(command, "regulatory_basis", []),
        completed_at=_get(command, "completed_at", datetime.now()),
    ).to_store_dict()

    fraud_trigger = FraudScreeningRequested(
        application_id=app_id,
        requested_at=datetime.now(),
        triggered_by_event_id=session_id,
    ).to_store_dict()

    # 4) Append with OCC
    credit_version = await store.stream_version(credit_stream)
    correlation_id = _get(command, "correlation_id")
    causation_id = _get(command, "causation_id", session_id)
    meta = {"correlation_id": correlation_id} if correlation_id else None
    await store.append(
        stream_id=credit_stream,
        events=[decision_event],
        expected_version=credit_version,
        causation_id=causation_id,
        metadata=meta,
    )
    await store.append(
        stream_id=f"loan-{app_id}",
        events=[fraud_trigger],
        expected_version=app.version,
        causation_id=causation_id,
        metadata=meta,
    )

    return [decision_event, fraud_trigger]


async def handle_decision_generated(store, command: Any) -> list[dict]:
    """
    Append DecisionGenerated with confidence floor + causal chain validation.
    """
    app_id = _get(command, "application_id")
    if not app_id:
        raise DomainError("application_id is required")

    app = await LoanApplicationAggregate.load(store, app_id)
    if app.state not in (ApplicationState.PENDING_DECISION, ApplicationState.COMPLIANCE_CHECK_COMPLETE):
        raise DomainError(f"Application {app_id} not ready for decision (state={app.state})")

    recommendation = _get(command, "recommendation")
    confidence = float(_get(command, "confidence", 0.0))
    contributing_sessions = _get(command, "contributing_sessions", [])
    orchestrator_session_id = _get(command, "orchestrator_session_id")
    orchestrator_agent_type = _get(command, "agent_type", "decision_orchestrator")

    if not orchestrator_session_id:
        raise DomainError("orchestrator_session_id is required")

    # Gas Town anchor: orchestrator session must start with AgentSessionStarted
    orch_stream = f"agent-{orchestrator_agent_type}-{orchestrator_session_id}"
    orch_session = await AgentSessionAggregate.load(store, orch_stream)
    if not orch_session.started:
        raise DomainError("Orchestrator session missing AgentSessionStarted anchor")
    orch_session.require_context_loaded()
    orch_session.require_model_version(_get(command, "model_version"))

    # Confidence floor enforcement
    if confidence < 0.60:
        recommendation = "REFER"

    # Compliance hard block: prevent decisions if blocked
    comp_events = await store.load_stream(f"compliance-{app_id}")
    if any(
        e.get("event_type") == "ComplianceRuleFailed"
        and e.get("payload", {}).get("is_hard_block")
        for e in comp_events
    ):
        raise DomainError("Compliance hard block present; decision is not allowed")
    if any(
        e.get("event_type") == "ComplianceCheckCompleted"
        and str(e.get("payload", {}).get("overall_verdict", "")).upper() == "BLOCKED"
        for e in comp_events
    ):
        raise DomainError("Compliance verdict BLOCKED; decision is not allowed")

    # Causal chain validation: each contributing session must belong to this application
    for sid in contributing_sessions:
        found = False
        for at in AgentType:
            stream_id = f"agent-{at.value}-{sid}"
            events = await store.load_stream(stream_id)
            if not events:
                continue
            first = events[0]
            if first.get("event_type") != "AgentSessionStarted":
                raise DomainError(f"Session {sid} missing AgentSessionStarted anchor")
            payload = first.get("payload", {})
            if payload.get("application_id") == app_id:
                found = True
                break
        if not found:
            raise DomainError(f"Contributing session {sid} is not tied to application {app_id}")

    event = DecisionGenerated(
        application_id=app_id,
        orchestrator_session_id=orchestrator_session_id,
        recommendation=recommendation,
        confidence=confidence,
        approved_amount_usd=_get(command, "approved_amount_usd"),
        conditions=_get(command, "conditions", []),
        executive_summary=_get(command, "executive_summary", ""),
        key_risks=_get(command, "key_risks", []),
        contributing_sessions=contributing_sessions,
        model_versions=_get(command, "model_versions", {}),
        generated_at=_get(command, "generated_at", datetime.now()),
    ).to_store_dict()

    correlation_id = _get(command, "correlation_id")
    causation_id = _get(command, "causation_id", orchestrator_session_id)
    meta = {"correlation_id": correlation_id} if correlation_id else None
    await store.append(
        stream_id=f"loan-{app_id}",
        events=[event],
        expected_version=app.version,
        causation_id=causation_id,
        metadata=meta,
    )
    return [event]


async def handle_application_approved(store, command: Any) -> list[dict]:
    """
    Append ApplicationApproved only if compliance stream shows all mandatory rules passed.
    """
    app_id = _get(command, "application_id")
    if not app_id:
        raise DomainError("application_id is required")

    app = await LoanApplicationAggregate.load(store, app_id)

    # Compliance dependency
    comp_stream = f"compliance-{app_id}"
    comp_events = await store.load_stream(comp_stream)
    initiated = next((e for e in comp_events if e.get("event_type") == "ComplianceCheckInitiated"), None)
    rules_required = initiated.get("payload", {}).get("rules_to_evaluate", []) if initiated else []
    passed_rules = {e.get("payload", {}).get("rule_id") for e in comp_events if e.get("event_type") == "ComplianceRulePassed"}
    failed_hard = any(
        e.get("event_type") == "ComplianceRuleFailed" and e.get("payload", {}).get("is_hard_block")
        for e in comp_events
    )
    missing = [r for r in rules_required if r not in passed_rules]
    if failed_hard or missing:
        raise DomainError(f"Compliance not satisfied. Missing={missing} hard_block={failed_hard}")

    event = ApplicationApproved(
        application_id=app_id,
        approved_amount_usd=Decimal(str(_get(command, "approved_amount_usd"))),
        interest_rate_pct=float(_get(command, "interest_rate_pct", 0.0)),
        term_months=int(_get(command, "term_months", 0)),
        conditions=_get(command, "conditions", []),
        approved_by=_get(command, "approved_by", "system"),
        effective_date=_get(command, "effective_date", datetime.now().strftime("%Y-%m-%d")),
        approved_at=_get(command, "approved_at", datetime.now()),
    ).to_store_dict()

    correlation_id = _get(command, "correlation_id")
    causation_id = _get(command, "causation_id")
    meta = {"correlation_id": correlation_id} if correlation_id else None
    await store.append(
        stream_id=f"loan-{app_id}",
        events=[event],
        expected_version=app.version,
        causation_id=causation_id,
        metadata=meta,
    )
    return [event]
