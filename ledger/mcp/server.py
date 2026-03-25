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
@mcp.tool(name="submit_application", description="Create a new loan application; requires unique application_id.")
async def _submit_application(payload: dict):
    return await submit_application(payload)

@mcp.tool(name="record_credit_analysis", description="Requires active agent session with context loaded.")
async def _record_credit_analysis(payload: dict):
    return await record_credit_analysis(payload)

@mcp.tool(name="run_credit_analysis_agent", description="Runs CreditAnalysisAgent end-to-end; may reuse an existing Gas Town session_id.")
async def _run_credit_analysis_agent(payload: dict):
    return await run_credit_analysis_agent(payload)

@mcp.tool(name="record_fraud_screening", description="Requires active agent session with context loaded; fraud_score in [0,1].")
async def _record_fraud_screening(payload: dict):
    return await record_fraud_screening(payload)

@mcp.tool(name="record_compliance_check", description="Requires valid rule_id list for active regulation set.")
async def _record_compliance_check(payload: dict):
    return await record_compliance_check(payload)

@mcp.tool(name="generate_decision", description="Requires all analyses complete; enforces confidence floor.")
async def _generate_decision(payload: dict):
    return await generate_decision(payload)

@mcp.tool(name="record_human_review", description="Requires reviewer_id; override_reason required on override.")
async def _record_human_review(payload: dict):
    return await record_human_review(payload)

@mcp.tool(name="start_agent_session", description="Starts a Gas Town session; must be first event for session stream.")
async def _start_agent_session(payload: dict):
    return await start_agent_session(payload)

@mcp.tool(name="run_integrity_check", description="Rate-limited integrity check (1/minute per entity).")
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
