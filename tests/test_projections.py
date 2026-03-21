"""
tests/test_projections.py
=========================
Phase 3 projection tests.
"""
from __future__ import annotations
import asyncio
import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import asyncpg

from ledger.event_store import EventStore
from ledger.projections import (
    ProjectionDaemon,
    ApplicationSummaryProjection,
    AgentPerformanceProjection,
    ComplianceAuditProjection,
)

DB_URL = os.environ.get("TEST_DB_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")

async def _prepare(conn, projection_names):
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS projection_checkpoints (projection_name TEXT PRIMARY KEY, last_position BIGINT NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    )
    for table in [
        "projection_application_summary",
        "projection_agent_performance",
        "projection_agent_session_index",
        "projection_compliance_audit",
        "projection_compliance_snapshots",
    ]:
        await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    max_pos = await conn.fetchval("SELECT COALESCE(MAX(global_position),0) FROM events")
    for name in projection_names:
        await conn.execute(
            "INSERT INTO projection_checkpoints(projection_name, last_position) "
            "VALUES($1,$2) ON CONFLICT (projection_name) DO UPDATE SET last_position=$2, updated_at=NOW()",
            name, int(max_pos),
        )
    return int(max_pos)


@pytest.mark.asyncio
async def test_projection_idempotency_and_checkpoint():
    store = EventStore(DB_URL)
    try:
        await store.connect()
    except Exception:
        pytest.skip("PostgreSQL not available for projection tests")

    conn = await asyncpg.connect(DB_URL)
    await _prepare(conn, ["application_summary"])
    await conn.close()

    app_id = f"APEX-PROJ-{uuid4().hex[:6]}"
    await store.append(
        f"loan-{app_id}",
        [{"event_type": "ApplicationSubmitted", "event_version": 1, "payload": {
            "application_id": app_id,
            "applicant_id": "COMP-001",
            "requested_amount_usd": "100000",
            "loan_purpose": "working_capital",
            "loan_term_months": 24,
            "submission_channel": "web",
            "contact_email": "x@example.com",
            "contact_name": "X",
            "submitted_at": datetime.now().isoformat(),
            "application_reference": app_id,
        }}],
        expected_version=-1,
    )

    daemon = ProjectionDaemon(DB_URL, [ApplicationSummaryProjection()])
    await daemon.run_once()
    await daemon.run_once()  # idempotent re-run

    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch(
        "SELECT * FROM projection_application_summary WHERE application_id=$1",
        app_id,
    )
    assert len(rows) == 1
    await conn.close()
    await store.close()


@pytest.mark.asyncio
async def test_checkpoint_reliability_restart():
    store = EventStore(DB_URL)
    try:
        await store.connect()
    except Exception:
        pytest.skip("PostgreSQL not available for projection tests")

    conn = await asyncpg.connect(DB_URL)
    await _prepare(conn, ["application_summary"])
    await conn.close()

    app_id = f"APEX-PROJ-{uuid4().hex[:6]}"
    await store.append(
        f"loan-{app_id}",
        [{"event_type": "ApplicationSubmitted", "event_version": 1, "payload": {
            "application_id": app_id,
            "applicant_id": "COMP-001",
            "requested_amount_usd": "100000",
            "loan_purpose": "working_capital",
            "loan_term_months": 24,
            "submission_channel": "web",
            "contact_email": "x@example.com",
            "contact_name": "X",
            "submitted_at": datetime.now().isoformat(),
            "application_reference": app_id,
        }}],
        expected_version=-1,
    )
    await store.append(
        f"credit-{app_id}",
        [{"event_type": "CreditAnalysisCompleted", "event_version": 2, "payload": {
            "application_id": app_id,
            "session_id": "sess-1",
            "decision": {"risk_tier": "LOW", "recommended_limit_usd": "50000", "confidence": 0.8, "rationale": "ok"},
            "model_version": "m1",
            "model_deployment_id": "d1",
            "input_data_hash": "h1",
            "analysis_duration_ms": 42,
            "completed_at": datetime.now().isoformat(),
        }}],
        expected_version=-1,
    )

    daemon = ProjectionDaemon(DB_URL, [ApplicationSummaryProjection()], batch_size=1)
    await daemon.run_once()  # process first event
    await daemon.close()

    daemon2 = ProjectionDaemon(DB_URL, [ApplicationSummaryProjection()], batch_size=10)
    await daemon2.run_once()  # resume

    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow(
        "SELECT risk_tier FROM projection_application_summary WHERE application_id=$1",
        app_id,
    )
    assert row is not None
    assert row["risk_tier"] == "LOW"
    await conn.close()
    await store.close()


@pytest.mark.asyncio
async def test_temporal_query_compliance():
    store = EventStore(DB_URL)
    try:
        await store.connect()
    except Exception:
        pytest.skip("PostgreSQL not available for projection tests")

    conn = await asyncpg.connect(DB_URL)
    await _prepare(conn, ["compliance_audit"])
    await conn.close()

    app_id = f"APEX-COMP-{uuid4().hex[:6]}"
    t1 = datetime.now() - timedelta(days=1, hours=2)
    t2 = datetime.now() - timedelta(days=1, hours=1)
    t3 = datetime.now() - timedelta(days=1)

    await store.append(
        f"compliance-{app_id}",
        [{"event_type": "ComplianceCheckInitiated", "event_version": 1, "payload": {
            "application_id": app_id,
            "session_id": "sess-c",
            "regulation_set_version": "2026-Q1",
            "rules_to_evaluate": ["REG-001"],
            "initiated_at": t1.isoformat(),
        }}],
        expected_version=-1,
    )
    await store.append(
        f"compliance-{app_id}",
        [{"event_type": "ComplianceRuleFailed", "event_version": 1, "payload": {
            "application_id": app_id,
            "session_id": "sess-c",
            "rule_id": "REG-003",
            "rule_name": "Jurisdiction Eligibility",
            "rule_version": "2026-Q1-v1",
            "failure_reason": "MT",
            "is_hard_block": True,
            "remediation_available": False,
            "evidence_hash": "h",
            "evaluated_at": t2.isoformat(),
        }}],
        expected_version=1,
    )
    await store.append(
        f"compliance-{app_id}",
        [{"event_type": "ComplianceCheckCompleted", "event_version": 1, "payload": {
            "application_id": app_id,
            "session_id": "sess-c",
            "rules_evaluated": 1,
            "rules_passed": 0,
            "rules_failed": 1,
            "rules_noted": 0,
            "has_hard_block": True,
            "overall_verdict": "BLOCKED",
            "completed_at": t3.isoformat(),
        }}],
        expected_version=2,
    )

    daemon = ProjectionDaemon(DB_URL, [ComplianceAuditProjection()])
    await daemon.run_once()

    conn = await asyncpg.connect(DB_URL)
    proj = ComplianceAuditProjection()
    state = await proj.get_compliance_at(conn, app_id, t2 + timedelta(minutes=1))
    assert state is not None
    assert state.get("has_hard_block") in (True, "true")
    await conn.close()
    await store.close()


@pytest.mark.asyncio
async def test_lag_and_rebuild():
    store = EventStore(DB_URL)
    try:
        await store.connect()
    except Exception:
        pytest.skip("PostgreSQL not available for projection tests")

    conn = await asyncpg.connect(DB_URL)
    await _prepare(conn, ["application_summary", "agent_performance", "compliance_audit"])
    await conn.close()

    app_id = f"APEX-LAG-{uuid4().hex[:6]}"
    events = [
        {"event_type": "ApplicationSubmitted", "event_version": 1, "payload": {
            "application_id": app_id,
            "applicant_id": "COMP-001",
            "requested_amount_usd": "100000",
            "loan_purpose": "working_capital",
            "loan_term_months": 24,
            "submission_channel": "web",
            "contact_email": "x@example.com",
            "contact_name": "X",
            "submitted_at": datetime.now().isoformat(),
            "application_reference": app_id,
        }},
    ]
    await store.append(f"loan-{app_id}", events, expected_version=-1)

    daemon = ProjectionDaemon(DB_URL, [
        ApplicationSummaryProjection(),
        AgentPerformanceProjection(),
        ComplianceAuditProjection(),
    ])
    await daemon.run_once()
    lag = await daemon.get_lag("application_summary")
    assert lag == 0

    await daemon.rebuild_from_scratch("application_summary")
    lag2 = await daemon.get_lag("application_summary")
    assert lag2 == 0
    await store.close()
