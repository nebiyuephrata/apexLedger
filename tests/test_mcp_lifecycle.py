import os
from datetime import datetime
from uuid import uuid4
import pytest
import asyncpg

from ledger.mcp import tools as mcp_tools
from ledger.mcp import resources as mcp_resources
from ledger.projections.application_summary import ApplicationSummaryProjection
from ledger.projections.agent_performance import AgentPerformanceProjection
from ledger.projections.compliance_audit import ComplianceAuditProjection
from ledger.projections.daemon import ProjectionDaemon

DB_URL = os.environ.get("TEST_DB_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")


@pytest.mark.asyncio
async def test_mcp_lifecycle_end_to_end():
    # Skip if DB unavailable
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.close()
    except Exception:
        pytest.skip("PostgreSQL not available for MCP lifecycle test")

    app_id = f"APEX-MCP-{uuid4().hex[:6]}"
    credit_sess = f"sess-credit-{uuid4().hex[:6]}"
    fraud_sess = f"sess-fraud-{uuid4().hex[:6]}"
    comp_sess = f"sess-comp-{uuid4().hex[:6]}"
    orch_sess = f"sess-orch-{uuid4().hex[:6]}"

    # start sessions
    await mcp_tools.start_agent_session({
        "session_id": credit_sess,
        "agent_type": "credit_analysis",
        "agent_id": "agent-credit-1",
        "application_id": app_id,
        "model_version": "m1",
        "context_source": "fresh",
        "context_token_count": 100,
    })
    await mcp_tools.start_agent_session({
        "session_id": fraud_sess,
        "agent_type": "fraud_detection",
        "agent_id": "agent-fraud-1",
        "application_id": app_id,
        "model_version": "m1",
        "context_source": "fresh",
        "context_token_count": 100,
    })
    await mcp_tools.start_agent_session({
        "session_id": comp_sess,
        "agent_type": "compliance",
        "agent_id": "agent-comp-1",
        "application_id": app_id,
        "model_version": "m1",
        "context_source": "fresh",
        "context_token_count": 100,
    })
    await mcp_tools.start_agent_session({
        "session_id": orch_sess,
        "agent_type": "decision_orchestrator",
        "agent_id": "agent-orch-1",
        "application_id": app_id,
        "model_version": "m1",
        "context_source": "fresh",
        "context_token_count": 100,
    })

    # submit application + auto document upload
    res = await mcp_tools.submit_application({
        "application_id": app_id,
        "applicant_id": "COMP-001",
        "requested_amount_usd": 100000,
        "loan_purpose": "working_capital",
        "loan_term_months": 24,
        "submission_channel": "web",
        "contact_email": "x@example.com",
        "contact_name": "X",
        "auto_document_upload": True,
    })
    assert res["ok"] is True, res

    # credit analysis
    res = await mcp_tools.record_credit_analysis({
        "application_id": app_id,
        "session_id": credit_sess,
        "agent_type": "credit_analysis",
        "decision": {
            "risk_tier": "LOW",
            "recommended_limit_usd": 50000,
            "confidence": 0.85,
            "rationale": "ok",
        },
        "model_version": "m1",
        "analysis_duration_ms": 10,
    })
    assert res["ok"] is True, res

    # fraud screening
    res = await mcp_tools.record_fraud_screening({
        "application_id": app_id,
        "session_id": fraud_sess,
        "agent_type": "fraud_detection",
        "fraud_score": 0.1,
        "risk_level": "LOW",
        "anomalies_found": 0,
        "recommendation": "CLEAR",
        "screening_model_version": "m1",
        "input_data_hash": "h1",
    })
    assert res["ok"] is True, res

    # compliance check
    res = await mcp_tools.record_compliance_check({
        "application_id": app_id,
        "session_id": comp_sess,
        "regulation_set_version": "2026-Q1",
        "rules": [
            {"rule_id": "REG-001", "status": "passed"},
            {"rule_id": "REG-002", "status": "passed"},
        ],
    })
    assert res["ok"] is True, res

    # decision
    res = await mcp_tools.generate_decision({
        "application_id": app_id,
        "orchestrator_session_id": orch_sess,
        "agent_type": "decision_orchestrator",
        "recommendation": "APPROVE",
        "confidence": 0.9,
        "executive_summary": "ok",
        "contributing_sessions": [credit_sess, fraud_sess, comp_sess],
        "model_versions": {"credit_analysis": "m1", "fraud_detection": "m1", "compliance": "m1"},
    })
    assert res["ok"] is True, res

    # human review final approval
    res = await mcp_tools.record_human_review({
        "application_id": app_id,
        "reviewer_id": "human-1",
        "final_decision": "APPROVE",
        "approved_amount_usd": 50000,
        "interest_rate_pct": 5.0,
        "term_months": 24,
    })
    assert res["ok"] is True

    # update projections
    daemon = ProjectionDaemon(DB_URL, [
        ApplicationSummaryProjection(),
        AgentPerformanceProjection(),
        ComplianceAuditProjection(),
    ])
    await daemon.run_once()
    await daemon.close()

    # query resources
    app_view = await mcp_resources.get_application_summary(app_id)
    assert app_view is not None
    assert app_view["application_id"] == app_id

    comp_view = await mcp_resources.get_compliance_view(app_id)
    assert comp_view is not None
    assert comp_view.get("overall_verdict") in ("CLEAR", "BLOCKED")
