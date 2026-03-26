from __future__ import annotations

try:
    from fastmcp import FastMCP
except Exception:  # pragma: no cover - fallback for environments without fastmcp
    class FastMCP:  # minimal stub to keep imports working
        def __init__(self, *args, **kwargs):
            pass
        def tool(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator
        def resource(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

from .tools import (
    submit_application,
    record_credit_analysis,
    run_credit_analysis_agent,
    run_document_processing_agent,
    run_fraud_detection_agent,
    run_compliance_agent,
    run_decision_orchestrator_agent,
    record_fraud_screening,
    record_compliance_check,
    generate_decision,
    record_human_review,
    start_agent_session,
    run_integrity_check,
)
from .resources import (
    get_application_summary,
    get_compliance_view,
    get_audit_trail,
    get_agent_performance,
    get_agent_session,
    get_health,
)

mcp = FastMCP("ledger")

# Tools (Command side)
@mcp.tool(name="submit_application", description="Create a new loan application. Preconditions: application_id must be new, applicant_id must exist in the applicant registry context, and requested_amount_usd plus loan_purpose must be provided. If the tool fails with an OptimisticConcurrencyError, reload and retry.")
async def _submit_application(payload: dict):
    return await submit_application(payload)

@mcp.tool(name="record_credit_analysis", description="Record a completed credit analysis. Preconditions: call start_agent_session first, reuse the same session_id, ensure the session has context loaded, and only call this when the loan is awaiting credit analysis. If you receive MODEL_VERSION_LOCKED, stop and wait for a human override.")
async def _record_credit_analysis(payload: dict):
    return await record_credit_analysis(payload)

@mcp.tool(name="run_credit_analysis_agent", description="Run CreditAnalysisAgent end-to-end. Preconditions: the application must already have document facts available and any provided session_id must belong to the same application.")
async def _run_credit_analysis_agent(payload: dict):
    return await run_credit_analysis_agent(payload)

@mcp.tool(name="run_document_processing_agent", description="Run DocumentProcessingAgent end-to-end. Preconditions: the application must already be in the document-uploaded stage and the referenced files must be reachable by the extraction service.")
async def _run_document_processing_agent(payload: dict):
    return await run_document_processing_agent(payload)

@mcp.tool(name="run_fraud_detection_agent", description="Run FraudDetectionAgent end-to-end. Preconditions: the application must already have extracted facts and any provided session_id must belong to the same application.")
async def _run_fraud_detection_agent(payload: dict):
    return await run_fraud_detection_agent(payload)

@mcp.tool(name="run_compliance_agent", description="Run ComplianceAgent end-to-end. Preconditions: the application must already be ready for compliance evaluation and any provided session_id must belong to the same application.")
async def _run_compliance_agent(payload: dict):
    return await run_compliance_agent(payload)

@mcp.tool(name="run_decision_orchestrator_agent", description="Run DecisionOrchestratorAgent end-to-end. Preconditions: credit, fraud, and compliance outputs must already exist and any provided session_id must belong to the same application.")
async def _run_decision_orchestrator_agent(payload: dict):
    return await run_decision_orchestrator_agent(payload)

@mcp.tool(name="record_fraud_screening", description="Record a completed fraud screening. Preconditions: call start_agent_session first, reuse the same session_id, ensure the session has context loaded, and provide fraud_score between 0.0 and 1.0.")
async def _record_fraud_screening(payload: dict):
    return await record_fraud_screening(payload)

@mcp.tool(name="record_compliance_check", description="Record deterministic compliance rule results. Preconditions: provide only valid rule_ids for the active regulation set and use a session_id that belongs to the same application.")
async def _record_compliance_check(payload: dict):
    return await record_compliance_check(payload)

@mcp.tool(name="generate_decision", description="Generate an orchestrated recommendation. Preconditions: start an orchestrator session first, ensure credit, fraud, and compliance are complete, and supply contributing_sessions tied to the same application. The tool will automatically force REFER when confidence is below 0.60.")
async def _generate_decision(payload: dict):
    return await generate_decision(payload)

@mcp.tool(name="record_human_review", description="Record the final human review decision. Preconditions: reviewer_id is required, and if final_decision differs from the AI recommendation you must also provide override_reason.")
async def _record_human_review(payload: dict):
    return await record_human_review(payload)

@mcp.tool(name="start_agent_session", description="Start a Gas Town session. Preconditions: this must be the first write for the session stream and you must provide session_id, agent_type, agent_id, application_id, and model_version.")
async def _start_agent_session(payload: dict):
    return await start_agent_session(payload)

@mcp.tool(name="run_integrity_check", description="Run an audit integrity check. Preconditions: this action is rate limited to once per minute per entity and should be used by privileged audit or compliance workflows.")
async def _run_integrity_check(payload: dict):
    return await run_integrity_check(payload)


# Resources (Query side)
@mcp.resource("ledger://applications/{id}")
async def _application_summary(id: str):
    return await get_application_summary(id)

@mcp.resource("ledger://applications/{id}/compliance")
async def _compliance_view(id: str, as_of: str | None = None):
    return await get_compliance_view(id, as_of=as_of)

@mcp.resource("ledger://applications/{id}/audit-trail")
async def _audit_trail(id: str):
    return await get_audit_trail(id)

@mcp.resource("ledger://agents/{id}/performance")
async def _agent_performance(id: str):
    return await get_agent_performance(id)

@mcp.resource("ledger://agents/{id}/sessions/{session_id}")
async def _agent_session(id: str, session_id: str):
    return await get_agent_session(id, session_id)

@mcp.resource("ledger://ledger/health")
async def _health():
    return await get_health()
