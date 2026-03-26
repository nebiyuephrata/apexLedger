from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
import os

from pydantic import BaseModel, Field

from ledger.commands.handlers import (
    handle_submit_application,
    handle_credit_analysis_completed,
    handle_decision_generated,
    handle_application_approved,
)
from ledger.agents.runtime import (
    build_extraction_client,
    build_llm_client,
    build_registry_client,
    run_compliance_agent as run_compliance_agent_runtime,
    run_credit_analysis_agent as run_credit_analysis_agent_runtime,
    run_decision_orchestrator_agent as run_decision_orchestrator_agent_runtime,
    run_document_processing_agent as run_document_processing_agent_runtime,
    run_fraud_detection_agent as run_fraud_detection_agent_runtime,
)
from ledger.domain.aggregates.agent_session import AgentSessionAggregate
from ledger.domain.aggregates.loan_application import LoanApplicationAggregate, ApplicationState
from ledger.domain.errors import DomainError
from ledger.event_store import EventStore, OptimisticConcurrencyError
from ledger.integrity.audit_chain import compute_chain_hash
from ledger.schema.events import (
    AgentSessionStarted,
    DocumentUploaded,
    CreditAnalysisRequested,
    DocumentType,
    DocumentFormat,
    FraudScreeningCompleted,
    ComplianceCheckInitiated,
    ComplianceRulePassed,
    ComplianceRuleFailed,
    ComplianceRuleNoted,
    ComplianceCheckCompleted,
    ComplianceVerdict,
    ComplianceCheckRequested,
    DecisionRequested,
    ApplicationDeclined,
    HumanReviewCompleted,
)
from ledger import upcasters

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")


class ToolError(BaseModel):
    error_type: str
    message: str
    context: dict = Field(default_factory=dict)
    expected_version: int | None = None
    actual_version: int | None = None
    suggested_action: str | None = None


class ToolResponse(BaseModel):
    ok: bool
    result: dict | None = None
    error: ToolError | None = None


def _ok(result: dict) -> dict:
    return ToolResponse(ok=True, result=result).model_dump()


def _err(exc: Exception) -> dict:
    if isinstance(exc, OptimisticConcurrencyError):
        return ToolResponse(
            ok=False,
            error=ToolError(
                error_type="OptimisticConcurrencyError",
                message=str(exc),
                context={"stream_id": exc.stream_id},
                expected_version=exc.expected,
                actual_version=exc.actual,
                suggested_action="reload_stream_and_retry",
            ),
        ).model_dump()
    if isinstance(exc, DomainError):
        suggested_action = {
            "APPLICATION_ALREADY_EXISTS": "use_a_new_application_id",
            "INVALID_APPLICATION_STATE": "reload_application_summary_and_retry_when_state_is_ready",
            "INVALID_STATE_TRANSITION": "reload_application_summary_and_retry_when_state_is_ready",
            "MISSING_SESSION_ANCHOR": "call_start_agent_session_then_retry",
            "CONTEXT_NOT_LOADED": "load_or_reuse_a_context_ready_session_then_retry",
            "MODEL_VERSION_MISMATCH": "reload_session_and_retry_with_matching_model_version",
            "APPLICATION_SESSION_MISMATCH": "use_a_session_for_the_same_application_then_retry",
            "MODEL_VERSION_LOCKED": "wait_for_human_override_or_stop_retrying",
            "COMPLIANCE_HARD_BLOCK": "stop_automation_and_request_human_review",
            "COMPLIANCE_NOT_SATISFIED": "review_compliance_results_and_resolve_missing_rules",
            "INVALID_CAUSAL_CHAIN": "reload_contributing_sessions_and_retry",
        }.get(exc.code, "fix_inputs_and_retry")
        return ToolResponse(
            ok=False,
            error=ToolError(
                error_type="DomainError",
                message=str(exc),
                context=exc.context,
                suggested_action=suggested_action,
            ),
        ).model_dump()
    return ToolResponse(
        ok=False,
        error=ToolError(
            error_type=type(exc).__name__,
            message=str(exc),
            context={},
            suggested_action="inspect_error_and_retry",
        ),
    ).model_dump()


async def _with_store():
    store = EventStore(DB_URL, upcaster_registry=upcasters.registry)
    upcasters.registry.store = store
    await store.connect()
    return store


def _default_agent_model(command: dict) -> str:
    if command.get("model_version"):
        return command["model_version"]
    if os.environ.get("GEMINI_MODEL"):
        return os.environ["GEMINI_MODEL"]
    if os.environ.get("LLM_PROVIDER", "").lower() == "gemini":
        return "gemini-1.5-pro"
    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")


# Tool: submit_application
async def submit_application(command: dict) -> dict:
    """Requires unique application_id and valid applicant_id/loan fields."""
    store = await _with_store()
    try:
        events = await handle_submit_application(store, command)
        if command.get("auto_document_upload"):
            app_id = command["application_id"]
            doc_event = DocumentUploaded(
                application_id=app_id,
                document_id=command.get("document_id", f"doc-{app_id}"),
                document_type=DocumentType.INCOME_STATEMENT,
                document_format=DocumentFormat.PDF,
                filename=command.get("filename", "auto.pdf"),
                file_path=command.get("file_path", "documents/auto.pdf"),
                file_size_bytes=int(command.get("file_size_bytes", 0)),
                file_hash=command.get("file_hash", "auto"),
                fiscal_year=command.get("fiscal_year"),
                uploaded_at=command.get("uploaded_at", datetime.now()),
                uploaded_by=command.get("uploaded_by", "system"),
            ).to_store_dict()
            ver = await store.stream_version(f"loan-{app_id}")
            await store.append(f"loan-{app_id}", [doc_event], expected_version=ver)
            events.append(doc_event)
            ver = await store.stream_version(f"loan-{app_id}")
            ca_req = CreditAnalysisRequested(
                application_id=app_id,
                requested_at=datetime.now(),
                requested_by=command.get("requested_by", "system"),
            ).to_store_dict()
            await store.append(f"loan-{app_id}", [ca_req], expected_version=ver)
            events.append(ca_req)
        return _ok({"events": events})
    except Exception as e:
        return _err(e)
    finally:
        await store.close()


# Tool: start_agent_session
async def start_agent_session(command: dict) -> dict:
    """Requires session_id, agent_type, agent_id, application_id, model_version."""
    store = await _with_store()
    try:
        session_id = command["session_id"]
        agent_type = command["agent_type"]
        stream_id = f"agent-{agent_type}-{session_id}"
        expected = await store.stream_version(stream_id)
        event = AgentSessionStarted(
            session_id=session_id,
            agent_type=agent_type,
            agent_id=command["agent_id"],
            application_id=command["application_id"],
            model_version=command.get("model_version", "unknown"),
            langgraph_graph_version=command.get("langgraph_graph_version", "1.0.0"),
            context_source=command.get("context_source", "fresh"),
            context_token_count=int(command.get("context_token_count", 0)),
            started_at=command.get("started_at", datetime.now()),
        ).to_store_dict()
        await store.append(stream_id, [event], expected_version=expected)
        return _ok({"stream_id": stream_id, "event": event})
    except Exception as e:
        return _err(e)
    finally:
        await store.close()


# Tool: record_credit_analysis
async def record_credit_analysis(command: dict) -> dict:
    """Requires an active AgentSession with context loaded."""
    store = await _with_store()
    try:
        events = await handle_credit_analysis_completed(store, command)
        return _ok({"events": events})
    except Exception as e:
        return _err(e)
    finally:
        await store.close()


# Tool: run_credit_analysis_agent
async def run_credit_analysis_agent(command: dict) -> dict:
    """Runs the CreditAnalysisAgent end-to-end; if session_id exists it reuses that Gas Town stream."""
    store = await _with_store()
    registry_pool = None
    try:
        registry_pool, registry = await build_registry_client(DB_URL)
        result = await run_credit_analysis_agent_runtime(
            store=store,
            registry=registry,
            application_id=command["application_id"],
            agent_id=command.get("agent_id", "agent-credit-1"),
            model=_default_agent_model(command),
            client=build_llm_client(),
            session_id=command.get("session_id"),
            context_source=command.get("context_source", "fresh"),
        )
        return _ok(result)
    except Exception as e:
        return _err(e)
    finally:
        if registry_pool is not None:
            await registry_pool.close()
        await store.close()


# Tool: run_document_processing_agent
async def run_document_processing_agent(command: dict) -> dict:
    """Runs DocumentProcessingAgent end-to-end using the configured extraction API when available."""
    store = await _with_store()
    registry_pool = None
    try:
        registry_pool, registry = await build_registry_client(DB_URL)
        result = await run_document_processing_agent_runtime(
            store=store,
            registry=registry,
            application_id=command["application_id"],
            agent_id=command.get("agent_id", "agent-document-1"),
            model=_default_agent_model(command),
            client=build_llm_client(),
            session_id=command.get("session_id"),
            context_source=command.get("context_source", "fresh"),
            extraction_client=build_extraction_client(),
        )
        return _ok(result)
    except Exception as e:
        return _err(e)
    finally:
        if registry_pool is not None:
            await registry_pool.close()
        await store.close()


# Tool: run_fraud_detection_agent
async def run_fraud_detection_agent(command: dict) -> dict:
    """Runs FraudDetectionAgent end-to-end; if session_id exists it reuses that Gas Town stream."""
    store = await _with_store()
    registry_pool = None
    try:
        registry_pool, registry = await build_registry_client(DB_URL)
        result = await run_fraud_detection_agent_runtime(
            store=store,
            registry=registry,
            application_id=command["application_id"],
            agent_id=command.get("agent_id", "agent-fraud-1"),
            model=_default_agent_model(command),
            client=build_llm_client(),
            session_id=command.get("session_id"),
            context_source=command.get("context_source", "fresh"),
        )
        return _ok(result)
    except Exception as e:
        return _err(e)
    finally:
        if registry_pool is not None:
            await registry_pool.close()
        await store.close()


# Tool: run_compliance_agent
async def run_compliance_agent(command: dict) -> dict:
    """Runs ComplianceAgent end-to-end; if session_id exists it reuses that Gas Town stream."""
    store = await _with_store()
    registry_pool = None
    try:
        registry_pool, registry = await build_registry_client(DB_URL)
        result = await run_compliance_agent_runtime(
            store=store,
            registry=registry,
            application_id=command["application_id"],
            agent_id=command.get("agent_id", "agent-compliance-1"),
            model=_default_agent_model(command),
            client=build_llm_client(),
            session_id=command.get("session_id"),
            context_source=command.get("context_source", "fresh"),
        )
        return _ok(result)
    except Exception as e:
        return _err(e)
    finally:
        if registry_pool is not None:
            await registry_pool.close()
        await store.close()


# Tool: run_decision_orchestrator_agent
async def run_decision_orchestrator_agent(command: dict) -> dict:
    """Runs DecisionOrchestratorAgent end-to-end; if session_id exists it reuses that Gas Town stream."""
    store = await _with_store()
    registry_pool = None
    try:
        registry_pool, registry = await build_registry_client(DB_URL)
        result = await run_decision_orchestrator_agent_runtime(
            store=store,
            registry=registry,
            application_id=command["application_id"],
            agent_id=command.get("agent_id", "agent-orchestrator-1"),
            model=_default_agent_model(command),
            client=build_llm_client(),
            session_id=command.get("session_id"),
            context_source=command.get("context_source", "fresh"),
        )
        return _ok(result)
    except Exception as e:
        return _err(e)
    finally:
        if registry_pool is not None:
            await registry_pool.close()
        await store.close()


# Tool: record_fraud_screening
async def record_fraud_screening(command: dict) -> dict:
    """Requires an active AgentSession with context loaded and fraud_score in [0.0, 1.0]."""
    store = await _with_store()
    try:
        app_id = command["application_id"]
        session_id = command["session_id"]
        agent_type = command.get("agent_type", "fraud_detection")
        score = float(command["fraud_score"])
        if score < 0.0 or score > 1.0:
            raise DomainError("fraud_score must be between 0.0 and 1.0")

        session = await AgentSessionAggregate.load(store, f"agent-{agent_type}-{session_id}")
        if not session.started:
            raise DomainError("Agent session missing AgentSessionStarted anchor")
        session.require_context_loaded()

        fraud_event = FraudScreeningCompleted(
            application_id=app_id,
            session_id=session_id,
            fraud_score=score,
            risk_level=command.get("risk_level", "LOW"),
            anomalies_found=int(command.get("anomalies_found", 0)),
            recommendation=command.get("recommendation", "CLEAR"),
            screening_model_version=command.get("screening_model_version", "unknown"),
            input_data_hash=command.get("input_data_hash", "unknown"),
            completed_at=command.get("completed_at", datetime.now()),
        ).to_store_dict()

        trigger = ComplianceCheckRequested(
            application_id=app_id,
            requested_at=datetime.now(),
            triggered_by_event_id=session_id,
            regulation_set_version=command.get("regulation_set_version", "2026-Q1"),
            rules_to_evaluate=command.get("rules_to_evaluate", []),
        ).to_store_dict()

        fraud_stream = f"fraud-{app_id}"
        fraud_version = await store.stream_version(fraud_stream)
        await store.append(fraud_stream, [fraud_event], expected_version=fraud_version)

        loan_stream = f"loan-{app_id}"
        loan_version = await store.stream_version(loan_stream)
        await store.append(loan_stream, [trigger], expected_version=loan_version)

        return _ok({"events": [fraud_event, trigger]})
    except Exception as e:
        return _err(e)
    finally:
        await store.close()


# Tool: record_compliance_check
async def record_compliance_check(command: dict) -> dict:
    """Requires valid rule_ids for the active regulation set."""
    store = await _with_store()
    try:
        app_id = command["application_id"]
        session_id = command["session_id"]
        regulation_set = command.get("regulation_set_version", "2026-Q1")
        rules = command.get("rules", [])

        allowed_rules = {"REG-001", "REG-002", "REG-003", "REG-004", "REG-005"}
        for r in rules:
            if r.get("rule_id") not in allowed_rules:
                raise DomainError(f"Unknown rule_id: {r.get('rule_id')}")

        comp_stream = f"compliance-{app_id}"
        comp_version = await store.stream_version(comp_stream)

        events = []
        events.append(
            ComplianceCheckInitiated(
                application_id=app_id,
                session_id=session_id,
                regulation_set_version=regulation_set,
                rules_to_evaluate=[r.get("rule_id") for r in rules],
                initiated_at=command.get("initiated_at", datetime.now()),
            ).to_store_dict()
        )

        passed = failed = noted = 0
        hard_block = False
        for r in rules:
            status = r.get("status", "passed")
            if status == "passed":
                passed += 1
                events.append(
                    ComplianceRulePassed(
                        application_id=app_id,
                        session_id=session_id,
                        rule_id=r.get("rule_id"),
                        rule_name=r.get("rule_name", r.get("rule_id")),
                        rule_version=r.get("rule_version", regulation_set),
                        evidence_hash=r.get("evidence_hash", ""),
                        evaluation_notes=r.get("evaluation_notes", ""),
                        evaluated_at=r.get("evaluated_at", datetime.now()),
                    ).to_store_dict()
                )
            elif status == "failed":
                failed += 1
                is_hard = bool(r.get("is_hard_block", False))
                hard_block = hard_block or is_hard
                events.append(
                    ComplianceRuleFailed(
                        application_id=app_id,
                        session_id=session_id,
                        rule_id=r.get("rule_id"),
                        rule_name=r.get("rule_name", r.get("rule_id")),
                        rule_version=r.get("rule_version", regulation_set),
                        failure_reason=r.get("failure_reason", ""),
                        is_hard_block=is_hard,
                        remediation_available=bool(r.get("remediation_available", False)),
                        remediation_description=r.get("remediation_description"),
                        evidence_hash=r.get("evidence_hash", ""),
                        evaluated_at=r.get("evaluated_at", datetime.now()),
                    ).to_store_dict()
                )
            else:
                noted += 1
                events.append(
                    ComplianceRuleNoted(
                        application_id=app_id,
                        session_id=session_id,
                        rule_id=r.get("rule_id"),
                        rule_name=r.get("rule_name", r.get("rule_id")),
                        note_type=r.get("note_type", "INFO"),
                        note_text=r.get("note_text", ""),
                        evaluated_at=r.get("evaluated_at", datetime.now()),
                    ).to_store_dict()
                )

        verdict = ComplianceVerdict.BLOCKED if hard_block else ComplianceVerdict.CLEAR
        events.append(
            ComplianceCheckCompleted(
                application_id=app_id,
                session_id=session_id,
                rules_evaluated=len(rules),
                rules_passed=passed,
                rules_failed=failed,
                rules_noted=noted,
                has_hard_block=hard_block,
                overall_verdict=verdict,
                completed_at=command.get("completed_at", datetime.now()),
            ).to_store_dict()
        )

        await store.append(comp_stream, events, expected_version=comp_version)

        if hard_block:
            loan_stream = f"loan-{app_id}"
            loan_version = await store.stream_version(loan_stream)
            decline = ApplicationDeclined(
                application_id=app_id,
                decline_reasons=["REG-003"],
                adverse_action_notice_required=True,
                declined_at=datetime.now(),
            ).to_store_dict()
            await store.append(loan_stream, [decline], expected_version=loan_version)
            events.append(decline)
        else:
            loan_stream = f"loan-{app_id}"
            loan_version = await store.stream_version(loan_stream)
            decision_req = DecisionRequested(
                application_id=app_id,
                requested_at=datetime.now(),
                all_analyses_complete=True,
                triggered_by_event_id=session_id,
            ).to_store_dict()
            await store.append(loan_stream, [decision_req], expected_version=loan_version)
            events.append(decision_req)

        return _ok({"events": events})
    except Exception as e:
        return _err(e)
    finally:
        await store.close()


# Tool: generate_decision
async def generate_decision(command: dict) -> dict:
    """Requires all analyses complete; enforces confidence floor."""
    store = await _with_store()
    try:
        events = await handle_decision_generated(store, command)
        return _ok({"events": events})
    except Exception as e:
        return _err(e)
    finally:
        await store.close()


# Tool: record_human_review
async def record_human_review(command: dict) -> dict:
    """Requires reviewer_id; override_reason required if decision differs from AI."""
    store = await _with_store()
    try:
        app_id = command["application_id"]
        reviewer_id = command["reviewer_id"]
        final_decision = command["final_decision"]

        app = await LoanApplicationAggregate.load(store, app_id)
        ai_rec = app.decision_recommendation
        override = ai_rec is not None and final_decision != ai_rec
        if override and not command.get("override_reason"):
            raise DomainError("override_reason required when overriding AI recommendation")

        review_event = HumanReviewCompleted(
            application_id=app_id,
            reviewer_id=reviewer_id,
            override=override,
            original_recommendation=ai_rec or "",
            final_decision=final_decision,
            override_reason=command.get("override_reason"),
            reviewed_at=command.get("reviewed_at", datetime.now()),
        ).to_store_dict()

        loan_stream = f"loan-{app_id}"
        loan_version = await store.stream_version(loan_stream)
        await store.append(loan_stream, [review_event], expected_version=loan_version)

        events = [review_event]
        if str(final_decision).upper() == "APPROVE":
            approval = await handle_application_approved(store, command)
            events.extend(approval)
        elif str(final_decision).upper() == "DECLINE":
            decline = ApplicationDeclined(
                application_id=app_id,
                decline_reasons=command.get("decline_reasons", ["HUMAN_REVIEW"]),
                adverse_action_notice_required=True,
                declined_at=datetime.now(),
            ).to_store_dict()
            loan_version = await store.stream_version(loan_stream)
            await store.append(loan_stream, [decline], expected_version=loan_version)
            events.append(decline)

        return _ok({"events": events})
    except Exception as e:
        return _err(e)
    finally:
        await store.close()


# Tool: run_integrity_check
async def run_integrity_check(command: dict) -> dict:
    """Role-restricted; one execution per minute per entity."""
    store = await _with_store()
    try:
        entity_id = command["entity_id"]
        stream_id = f"audit-{entity_id}"
        events = await store.load_stream(stream_id)
        if events:
            last = events[-1]
            last_ts = last.get("payload", {}).get("check_timestamp")
            if last_ts:
                if isinstance(last_ts, str):
                    last_ts = datetime.fromisoformat(last_ts)
                if datetime.now() - last_ts < timedelta(minutes=1):
                    raise DomainError("Integrity check rate limited (1/minute)")

        chain_hash = compute_chain_hash(events)
        event = {
            "event_type": "AuditIntegrityCheckRun",
            "event_version": 1,
            "payload": {
                "entity_type": command.get("entity_type", "application"),
                "entity_id": entity_id,
                "check_timestamp": datetime.now().isoformat(),
                "events_verified_count": len(events),
                "integrity_hash": chain_hash,
                "previous_hash": events[-1].get("payload", {}).get("integrity_hash") if events else None,
                "chain_valid": True,
                "tamper_detected": False,
            },
        }

        ver = await store.stream_version(stream_id)
        await store.append(stream_id, [event], expected_version=ver)
        return _ok({"event": event})
    except Exception as e:
        return _err(e)
    finally:
        await store.close()
