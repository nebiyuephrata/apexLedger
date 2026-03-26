from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from ledger import upcasters
from ledger.event_store import EventStore, InMemoryEventStore, OptimisticConcurrencyError
from ledger.integrity.gas_town import reconstruct_agent_context
from ledger.mcp import resources as mcp_resources
from ledger.mcp import tools as mcp_tools
from ledger.mcp.tools import run_integrity_check
from ledger.projections.application_summary import ApplicationSummaryProjection
from ledger.projections.agent_performance import AgentPerformanceProjection
from ledger.projections.compliance_audit import ComplianceAuditProjection
from ledger.projections.daemon import ProjectionDaemon

DB_URL = os.environ.get("TEST_DB_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")


def _print_header(title: str) -> None:
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)


def _event_summary(event: dict) -> str:
    payload = event.get("payload") or {}
    metadata = event.get("metadata") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    causal_bits = []
    if metadata.get("correlation_id"):
        causal_bits.append(f"corr={metadata['correlation_id']}")
    if metadata.get("causation_id"):
        causal_bits.append(f"cause={metadata['causation_id']}")
    if payload.get("triggered_by_event_id"):
        causal_bits.append(f"triggered_by={payload['triggered_by_event_id']}")
    if payload.get("orchestrator_session_id"):
        causal_bits.append(f"orch_session={payload['orchestrator_session_id']}")
    if payload.get("contributing_sessions"):
        causal_bits.append(f"contributors={payload['contributing_sessions']}")
    return ", ".join(causal_bits) if causal_bits else "-"


async def _connect_or_skip():
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.close()
    except Exception:
        pytest.skip("PostgreSQL not available for demo showcase")


@pytest.mark.asyncio
async def test_demo_week_standard():
    await _connect_or_skip()

    started = time.perf_counter()
    app_id = f"DEMO-WEEK-{uuid4().hex[:6]}"
    correlation_id = f"corr-{uuid4().hex[:6]}"
    credit_sess = f"sess-credit-{uuid4().hex[:6]}"
    fraud_sess = f"sess-fraud-{uuid4().hex[:6]}"
    comp_sess = f"sess-comp-{uuid4().hex[:6]}"
    orch_sess = f"sess-orch-{uuid4().hex[:6]}"

    _print_header("Week Standard: Complete Decision History")
    print(f"application_id={app_id}")
    print(f"correlation_id={correlation_id}")

    for sess_id, agent_type, agent_id in [
        (credit_sess, "credit_analysis", "agent-credit-1"),
        (fraud_sess, "fraud_detection", "agent-fraud-1"),
        (comp_sess, "compliance", "agent-comp-1"),
        (orch_sess, "decision_orchestrator", "agent-orch-1"),
    ]:
        result = await mcp_tools.start_agent_session(
            {
                "session_id": sess_id,
                "agent_type": agent_type,
                "agent_id": agent_id,
                "application_id": app_id,
                "model_version": "m1",
                "context_source": "fresh",
                "context_token_count": 100,
            }
        )
        print(f"start_agent_session[{agent_type}] ok={result['ok']} session_id={sess_id}")

    submit = await mcp_tools.submit_application(
        {
            "application_id": app_id,
            "applicant_id": "COMP-001",
            "requested_amount_usd": 100000,
            "loan_purpose": "working_capital",
            "loan_term_months": 24,
            "submission_channel": "web",
            "contact_email": "demo@example.com",
            "contact_name": "Demo User",
            "auto_document_upload": True,
            "correlation_id": correlation_id,
            "causation_id": "demo-submit",
        }
    )
    print(
        "submit_application "
        f"requested_amount_usd=100000 loan_purpose=working_capital ok={submit['ok']}"
    )

    credit = await mcp_tools.record_credit_analysis(
        {
            "application_id": app_id,
            "session_id": credit_sess,
            "agent_type": "credit_analysis",
            "decision": {
                "risk_tier": "LOW",
                "recommended_limit_usd": 50000,
                "confidence": 0.85,
                "rationale": "healthy cash flow",
            },
            "model_version": "m1",
            "analysis_duration_ms": 10,
            "correlation_id": correlation_id,
            "causation_id": credit_sess,
        }
    )
    print(
        "record_credit_analysis "
        "risk_tier=LOW recommended_limit_usd=50000 confidence=0.85 "
        f"ok={credit['ok']}"
    )

    fraud = await mcp_tools.record_fraud_screening(
        {
            "application_id": app_id,
            "session_id": fraud_sess,
            "agent_type": "fraud_detection",
            "fraud_score": 0.1,
            "risk_level": "LOW",
            "anomalies_found": 0,
            "recommendation": "CLEAR",
            "screening_model_version": "m1",
            "input_data_hash": "demo-h1",
        }
    )
    print("record_fraud_screening fraud_score=0.1 recommendation=CLEAR ok=%s" % fraud["ok"])

    compliance = await mcp_tools.record_compliance_check(
        {
            "application_id": app_id,
            "session_id": comp_sess,
            "regulation_set_version": "2026-Q1",
            "rules": [
                {"rule_id": "REG-001", "status": "passed"},
                {"rule_id": "REG-002", "status": "passed"},
            ],
        }
    )
    print("record_compliance_check rules=[REG-001:passed, REG-002:passed] ok=%s" % compliance["ok"])

    decision = await mcp_tools.generate_decision(
        {
            "application_id": app_id,
            "orchestrator_session_id": orch_sess,
            "agent_type": "decision_orchestrator",
            "recommendation": "APPROVE",
            "confidence": 0.9,
            "executive_summary": "credit, fraud, and compliance support approval",
            "contributing_sessions": [credit_sess, fraud_sess, comp_sess],
            "model_versions": {"credit_analysis": "m1", "fraud_detection": "m1", "compliance": "m1"},
            "correlation_id": correlation_id,
            "causation_id": orch_sess,
        }
    )
    print("generate_decision recommendation=APPROVE confidence=0.9 ok=%s" % decision["ok"])

    human = await mcp_tools.record_human_review(
        {
            "application_id": app_id,
            "reviewer_id": "human-1",
            "final_decision": "APPROVE",
            "approved_amount_usd": 50000,
            "interest_rate_pct": 5.0,
            "term_months": 24,
        }
    )
    print("record_human_review final_decision=APPROVE approved_amount_usd=50000 ok=%s" % human["ok"])

    integrity = await run_integrity_check(
        {
            "entity_id": app_id,
            "entity_type": "application",
            "check_timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    assert integrity["ok"] is True
    print(
        "run_integrity_check "
        f"chain_valid={integrity['result']['chain_valid']} "
        f"tamper_detected={integrity['result']['tamper_detected']} "
        f"events_verified_count={integrity['result']['events_verified_count']}"
    )

    daemon = ProjectionDaemon(
        DB_URL,
        [ApplicationSummaryProjection(), AgentPerformanceProjection(), ComplianceAuditProjection()],
    )
    await daemon.run_once()
    await daemon.close()

    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            """
            SELECT global_position, stream_id, stream_position, event_type, payload, metadata
            FROM events
            WHERE stream_id LIKE $1 OR payload->>'application_id' = $2
            ORDER BY global_position
            """,
            f"%{app_id}",
            app_id,
        )
    finally:
        await conn.close()

    print()
    print("global_pos | stream_id                                 | pos | event_type                 | causal_evidence")
    visible_types = set()
    for row in rows:
        event = dict(row)
        visible_types.add(event["event_type"])
        print(
            f"{event['global_position']:>10} | "
            f"{event['stream_id']:<40} | "
            f"{event['stream_position']:>3} | "
            f"{event['event_type']:<25} | "
            f"{_event_summary(event)}"
        )

    required_types = {
        "ApplicationSubmitted",
        "CreditAnalysisCompleted",
        "FraudScreeningCompleted",
        "ComplianceCheckCompleted",
        "HumanReviewCompleted",
        "ApplicationApproved",
        "AuditIntegrityCheckRun",
    }
    assert required_types.issubset(visible_types)

    elapsed = time.perf_counter() - started
    print()
    print(f"week_standard_seconds={elapsed:.2f}")
    assert elapsed < 60


@pytest.mark.asyncio
async def test_demo_concurrency_pressure():
    await _connect_or_skip()
    store = EventStore(DB_URL)
    await store.connect()
    try:
        _print_header("Concurrency Under Pressure")
        stream_id = f"loan-concurrency-demo-{uuid4().hex[:8]}"
        seed = [{"event_type": "Init", "event_version": 1, "payload": {"seq": i, "demo": True}} for i in range(3)]
        await store.append(stream_id, seed, expected_version=-1)
        print(f"stream_id={stream_id}")
        print("two concurrent tasks target expected_version=3")

        async def attempt(label: str):
            try:
                positions = await store.append(
                    stream_id,
                    [{"event_type": "CreditAnalysisCompleted", "event_version": 1, "payload": {"label": label}}],
                    expected_version=3,
                )
                return {"task": label, "ok": True, "positions": positions}
            except OptimisticConcurrencyError as exc:
                return {
                    "task": label,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "expected": exc.expected,
                    "actual": exc.actual,
                    "reloaded_version": await store.stream_version(stream_id),
                }

        results = await asyncio.gather(attempt("task-A"), attempt("task-B"))
        for result in results:
            print(result)

        events = await store.load_stream(stream_id)
        print(f"final_stream_length={len(events)}")
        print(f"final_stream_version={await store.stream_version(stream_id)}")
        print(f"winning_stream_position={events[-1]['stream_position']}")

        successes = [r for r in results if r["ok"]]
        errors = [r for r in results if not r["ok"]]
        assert len(successes) == 1
        assert len(errors) == 1
        assert errors[0]["error_type"] == "OptimisticConcurrencyError"
        assert len(events) == 4
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_demo_temporal_compliance_query():
    await _connect_or_skip()
    store = EventStore(DB_URL)
    await store.connect()
    app_id = f"DEMO-TEMP-{uuid4().hex[:6]}"
    t1 = datetime.now(timezone.utc) - timedelta(days=1, hours=2)
    t2 = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    t3 = datetime.now(timezone.utc) - timedelta(days=1)
    try:
        _print_header("Temporal Compliance Query")
        await store.append(
            f"compliance-{app_id}",
            [{
                "event_type": "ComplianceCheckInitiated",
                "event_version": 1,
                "payload": {
                    "application_id": app_id,
                    "session_id": "sess-temp",
                    "regulation_set_version": "2026-Q1",
                    "rules_to_evaluate": ["REG-003"],
                    "initiated_at": t1.isoformat(),
                },
            }],
            expected_version=-1,
        )
        await store.append(
            f"compliance-{app_id}",
            [{
                "event_type": "ComplianceRuleFailed",
                "event_version": 1,
                "payload": {
                    "application_id": app_id,
                    "session_id": "sess-temp",
                    "rule_id": "REG-003",
                    "rule_name": "Jurisdiction Eligibility",
                    "rule_version": "2026-Q1-v1",
                    "failure_reason": "MT",
                    "is_hard_block": True,
                    "remediation_available": False,
                    "evidence_hash": "demo-hash",
                    "evaluated_at": t2.isoformat(),
                },
            }],
            expected_version=1,
        )
        await store.append(
            f"compliance-{app_id}",
            [{
                "event_type": "ComplianceCheckCompleted",
                "event_version": 1,
                "payload": {
                    "application_id": app_id,
                    "session_id": "sess-temp",
                    "rules_evaluated": 1,
                    "rules_passed": 0,
                    "rules_failed": 1,
                    "rules_noted": 0,
                    "has_hard_block": True,
                    "overall_verdict": "BLOCKED",
                    "completed_at": t3.isoformat(),
                },
            }],
            expected_version=2,
        )

        daemon = ProjectionDaemon(DB_URL, [ComplianceAuditProjection()])
        await daemon.rebuild_from_scratch("compliance_audit")
        await daemon.close()

        conn = await asyncpg.connect(DB_URL)
        try:
            proj = ComplianceAuditProjection()
            past = await proj.get_compliance_at(conn, app_id, t1 + timedelta(minutes=30))
            current = await proj.get_compliance_at(conn, app_id, datetime.now(timezone.utc))
        finally:
            await conn.close()

        print(f"application_id={app_id}")
        print(f"as_of={(t1 + timedelta(minutes=30)).isoformat()}")
        print(f"past_state={past}")
        print(f"current_state={current}")
        assert past != current
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_demo_upcasting_immutability():
    await _connect_or_skip()
    _print_header("Upcasting and Immutability")
    store = EventStore(DB_URL, upcaster_registry=upcasters.registry)
    upcasters.registry.store = store
    await store.connect()
    stream_id = f"credit-upcast-demo-{uuid4().hex[:6]}"
    try:
        await store.append(
            stream_id,
            [{
                "event_type": "CreditAnalysisCompleted",
                "event_version": 1,
                "payload": {
                    "application_id": "APP-UPCAST-1",
                    "session_id": "sess-upcast",
                    "decision": {
                        "risk_tier": "LOW",
                        "recommended_limit_usd": "10000",
                        "confidence": 0.75,
                        "rationale": "ok",
                    },
                    "model_deployment_id": "d1",
                    "input_data_hash": "h1",
                    "analysis_duration_ms": 10,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            }],
            expected_version=-1,
        )

        conn = await asyncpg.connect(DB_URL)
        try:
            raw = await conn.fetchrow(
                "SELECT event_version, payload FROM events WHERE stream_id=$1 ORDER BY stream_position DESC LIMIT 1",
                stream_id,
            )
        finally:
            await conn.close()

        loaded = (await store.load_stream(stream_id))[-1]
        print(f"stream_id={stream_id}")
        print(f"raw_db_event_version={raw['event_version']}")
        print(f"raw_db_payload={raw['payload']}")
        print(f"loaded_event_version={loaded['event_version']}")
        print(f"loaded_payload={loaded['payload']}")

        assert raw["event_version"] == 1
        assert "model_version" not in raw["payload"]
        assert loaded["event_version"] == 2
        assert "model_version" in loaded["payload"]
        assert "confidence_score" in loaded["payload"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_demo_gas_town_recovery():
    _print_header("Gas Town Agent Memory Recovery")
    store = InMemoryEventStore()
    stream_id = f"agent-credit_analysis-demo-{uuid4().hex[:6]}"
    events = [
        {
            "event_type": "AgentSessionStarted",
            "event_version": 1,
            "payload": {
                "session_id": "sess-recover-1",
                "agent_type": "credit_analysis",
                "application_id": "APP-REC-1",
                "model_version": "m1",
                "context_source": "fresh",
                "started_at": "2026-03-23T10:00:00",
            },
        },
        {
            "event_type": "AgentNodeExecuted",
            "event_version": 1,
            "payload": {
                "session_id": "sess-recover-1",
                "agent_type": "credit_analysis",
                "node_name": "load_external_data",
                "node_sequence": 1,
            },
        },
        {
            "event_type": "AgentToolCalled",
            "event_version": 1,
            "payload": {
                "session_id": "sess-recover-1",
                "agent_type": "credit_analysis",
                "tool_name": "applicant_registry_lookup",
                "duration_ms": 12,
            },
        },
        {
            "event_type": "AgentNodeExecuted",
            "event_version": 1,
            "payload": {
                "session_id": "sess-recover-1",
                "agent_type": "credit_analysis",
                "node_name": "analyze_credit_risk",
                "node_sequence": 2,
            },
        },
        {
            "event_type": "DecisionGenerated",
            "event_version": 1,
            "payload": {
                "application_id": "APP-REC-1",
                "recommendation": "APPROVE",
                "confidence": 0.72,
            },
        },
    ]

    version = -1
    for event in events:
        positions = await store.append(stream_id, [event], expected_version=version)
        version = positions[-1]

    print("session_history:")
    for event in await store.load_stream(stream_id):
        print(f"  pos={event['stream_position'] + 1} type={event['event_type']} payload={event['payload']}")

    print("simulate_crash=true")
    context = await reconstruct_agent_context(store, stream_id, keep_last_n=3)
    print(f"reconstructed_context={context.model_dump()}")

    assert context.session_health_status == "NEEDS_RECONCILIATION"
    assert context.pending_work
    assert context.last_event_position == 5
