from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from ledger.api.auth import AuthContext, resolve_auth_context
from ledger.api.infra import (
    enforce_rate_limit,
    get_cached_or_load,
    invalidate_patterns,
    load_idempotency_record,
    save_idempotency_record,
    stable_fingerprint,
)
from ledger.api.policy import ApplicationAccessRecord, policy_engine
from ledger.mcp import resources as mcp_resources
from ledger.mcp import tools as mcp_tools

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")
IDEMPOTENCY_TTL_SECONDS = int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "86400"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
CACHE_TTL_APPLICATIONS = int(os.environ.get("CACHE_TTL_APPLICATIONS_SECONDS", "5"))
CACHE_TTL_COMPLIANCE = int(os.environ.get("CACHE_TTL_COMPLIANCE_SECONDS", "15"))
CACHE_TTL_HEALTH = int(os.environ.get("CACHE_TTL_HEALTH_SECONDS", "3"))
CACHE_TTL_AUDIT = int(os.environ.get("CACHE_TTL_AUDIT_SECONDS", "10"))

TOOL_DEFINITIONS: list[dict[str, str]] = [
    {
        "name": "submit_application",
        "description": "Create a new loan application.",
        "precondition": "application_id must be new and the caller must belong to the tenant that will own the application.",
    },
    {
        "name": "record_credit_analysis",
        "description": "Record a completed credit analysis.",
        "precondition": "call start_agent_session first and only use a session for the same application.",
    },
    {
        "name": "record_fraud_screening",
        "description": "Record a completed fraud screening.",
        "precondition": "use a context-ready agent session and provide fraud_score between 0.0 and 1.0.",
    },
    {
        "name": "record_compliance_check",
        "description": "Record deterministic compliance rule results.",
        "precondition": "the caller must have compliance permissions and all rule IDs must be valid.",
    },
    {
        "name": "generate_decision",
        "description": "Generate the orchestrated recommendation.",
        "precondition": "credit, fraud, and compliance outputs must exist before invocation.",
    },
    {
        "name": "record_human_review",
        "description": "Record the final human review decision.",
        "precondition": "reviewer_id is required and override_reason is required when diverging from the AI recommendation.",
    },
    {
        "name": "start_agent_session",
        "description": "Start a Gas Town agent session.",
        "precondition": "must be the first write on the session stream.",
    },
    {
        "name": "run_integrity_check",
        "description": "Verify the tamper-evident audit chain.",
        "precondition": "restricted to privileged audit/security workflows and rate limited to one run per minute per entity.",
    },
    {
        "name": "run_document_processing_agent",
        "description": "Run document processing end to end.",
        "precondition": "the application must already have uploaded documents.",
    },
    {
        "name": "run_credit_analysis_agent",
        "description": "Run credit analysis end to end.",
        "precondition": "document facts must already exist for the application.",
    },
    {
        "name": "run_fraud_detection_agent",
        "description": "Run fraud detection end to end.",
        "precondition": "document facts and historical registry context must be available.",
    },
    {
        "name": "run_compliance_agent",
        "description": "Run compliance evaluation end to end.",
        "precondition": "the application must already be ready for compliance evaluation.",
    },
    {
        "name": "run_decision_orchestrator_agent",
        "description": "Run the decision orchestrator end to end.",
        "precondition": "credit, fraud, and compliance results must already exist.",
    },
]

TOOL_HANDLERS: dict[str, Callable[[dict], Any]] = {
    "submit_application": mcp_tools.submit_application,
    "record_credit_analysis": mcp_tools.record_credit_analysis,
    "record_fraud_screening": mcp_tools.record_fraud_screening,
    "record_compliance_check": mcp_tools.record_compliance_check,
    "generate_decision": mcp_tools.generate_decision,
    "record_human_review": mcp_tools.record_human_review,
    "start_agent_session": mcp_tools.start_agent_session,
    "run_integrity_check": mcp_tools.run_integrity_check,
    "run_document_processing_agent": mcp_tools.run_document_processing_agent,
    "run_credit_analysis_agent": mcp_tools.run_credit_analysis_agent,
    "run_fraud_detection_agent": mcp_tools.run_fraud_detection_agent,
    "run_compliance_agent": mcp_tools.run_compliance_agent,
    "run_decision_orchestrator_agent": mcp_tools.run_decision_orchestrator_agent,
}


def create_app() -> FastAPI:
    app = FastAPI(title="The Ledger BFF", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in os.environ.get("LEDGER_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "ledger-bff"}

    @app.get("/api/session")
    async def session(auth: AuthContext = Depends(resolve_auth_context)) -> dict:
        return policy_engine.session_payload(auth)

    @app.get("/api/meta/actors")
    async def actor_profiles(auth: AuthContext = Depends(resolve_auth_context)) -> list[dict]:
        return policy_engine.actor_profiles()

    @app.get("/api/meta/commands")
    async def command_catalog(auth: AuthContext = Depends(resolve_auth_context)) -> list[dict]:
        allowed = set(policy_engine.session_payload(auth)["allowed_tools"])
        return [item for item in TOOL_DEFINITIONS if item["name"] in allowed or auth.is_internal]

    @app.get("/api/applications")
    async def applications(auth: AuthContext = Depends(resolve_auth_context)) -> list[dict]:
        policy_engine.ensure_view_allowed(auth, "applicant" if auth.role == "applicant" else "dashboard")
        cache_key = f"cache:{auth.org_id}:{auth.user_id}:applications"

        async def _load() -> list[dict]:
            conn = await asyncpg.connect(DB_URL)
            try:
                rows = await conn.fetch(
                    "SELECT application_id, state, applicant_id, tenant_id, owner_user_id, requested_amount_usd, approved_amount_usd, risk_tier, fraud_score, compliance_status, decision_recommendation, agent_sessions_completed, human_reviewer_id, final_decision_at, last_event_type, last_event_at FROM projection_application_summary ORDER BY last_event_at DESC NULLS LAST, application_id ASC"
                )
                return policy_engine.filter_applications(auth, [dict(row) for row in rows])
            finally:
                await conn.close()

        return await get_cached_or_load(cache_key, CACHE_TTL_APPLICATIONS, _load)

    @app.get("/api/applications/{application_id}")
    async def application_summary(application_id: str, auth: AuthContext = Depends(resolve_auth_context)) -> dict | None:
        cache_key = f"cache:{auth.org_id}:{auth.user_id}:applications:{application_id}"

        async def _load() -> dict | None:
            row = await mcp_resources.get_application_summary(application_id)
            if not row:
                return None
            record = ApplicationAccessRecord(
                application_id=application_id,
                applicant_id=row.get("applicant_id"),
                tenant_id=row.get("tenant_id"),
                owner_user_id=row.get("owner_user_id"),
            )
            policy_engine.ensure_application_visible(auth, record)
            return row

        return await get_cached_or_load(cache_key, CACHE_TTL_APPLICATIONS, _load)

    @app.get("/api/applications/{application_id}/compliance")
    async def compliance_view(application_id: str, as_of: str | None = None, auth: AuthContext = Depends(resolve_auth_context)) -> dict | None:
        policy_engine.ensure_view_allowed(auth, "compliance")
        summary = await mcp_resources.get_application_summary(application_id)
        if not summary:
            return None
        record = ApplicationAccessRecord(
            application_id=application_id,
            applicant_id=summary.get("applicant_id"),
            tenant_id=summary.get("tenant_id"),
            owner_user_id=summary.get("owner_user_id"),
        )
        policy_engine.ensure_application_visible(auth, record)
        cache_key = f"cache:{auth.org_id}:{auth.user_id}:compliance:{application_id}:{as_of or 'current'}"
        return await get_cached_or_load(cache_key, CACHE_TTL_COMPLIANCE, lambda: mcp_resources.get_compliance_view(application_id, as_of=as_of))

    @app.get("/api/applications/{application_id}/audit-trail")
    async def audit_trail(application_id: str, auth: AuthContext = Depends(resolve_auth_context)) -> list[dict]:
        policy_engine.ensure_view_allowed(auth, "audit-trail")
        summary = await mcp_resources.get_application_summary(application_id)
        if summary:
            record = ApplicationAccessRecord(
                application_id=application_id,
                applicant_id=summary.get("applicant_id"),
                tenant_id=summary.get("tenant_id"),
                owner_user_id=summary.get("owner_user_id"),
            )
            policy_engine.ensure_application_visible(auth, record)
        cache_key = f"cache:{auth.org_id}:{auth.user_id}:audit:{application_id}"
        return await get_cached_or_load(cache_key, CACHE_TTL_AUDIT, lambda: mcp_resources.get_audit_trail(application_id))

    @app.get("/api/agents/performance")
    async def agent_performance(agent_type: str | None = None, auth: AuthContext = Depends(resolve_auth_context)) -> list[dict]:
        policy_engine.ensure_view_allowed(auth, "admin")
        agent_id = agent_type or "decision_orchestrator"
        cache_key = f"cache:{auth.org_id}:{auth.user_id}:agent-performance:{agent_id}"
        return await get_cached_or_load(cache_key, CACHE_TTL_APPLICATIONS, lambda: mcp_resources.get_agent_performance(agent_id))

    @app.get("/api/agents/sessions")
    async def agent_sessions(auth: AuthContext = Depends(resolve_auth_context)) -> list[dict]:
        policy_engine.ensure_view_allowed(auth, "dashboard")
        conn = await asyncpg.connect(DB_URL)
        try:
            rows = await conn.fetch(
                "SELECT session_id, agent_type, application_id, model_version FROM projection_agent_session_index ORDER BY session_id DESC LIMIT 50"
            )
            items = []
            for row in rows:
                app_id = row["application_id"]
                summary = await mcp_resources.get_application_summary(app_id)
                if summary:
                    record = ApplicationAccessRecord(
                        application_id=app_id,
                        applicant_id=summary.get("applicant_id"),
                        tenant_id=summary.get("tenant_id"),
                        owner_user_id=summary.get("owner_user_id"),
                    )
                    try:
                        policy_engine.ensure_application_visible(auth, record)
                    except HTTPException:
                        continue
                items.append(
                    {
                        "session_id": row["session_id"],
                        "agent_type": row["agent_type"],
                        "application_id": app_id,
                        "last_node": "reconstruct_from_stream",
                        "status": "running",
                        "context_source": row["model_version"],
                    }
                )
            return items
        finally:
            await conn.close()

    @app.get("/api/ledger/health")
    async def ledger_health(auth: AuthContext = Depends(resolve_auth_context)) -> dict:
        cache_key = f"cache:{auth.org_id}:{auth.user_id}:ledger-health"
        return await get_cached_or_load(cache_key, CACHE_TTL_HEALTH, mcp_resources.get_health)

    @app.get("/api/ops/logs")
    async def ops_logs(limit: int = Query(default=50, le=200), auth: AuthContext = Depends(resolve_auth_context)) -> list[dict]:
        policy_engine.ensure_logs_allowed(auth)
        conn = await asyncpg.connect(DB_URL)
        try:
            outbox_pending = await conn.fetchval("SELECT COUNT(*) FROM outbox WHERE published_at IS NULL")
            checkpoints = await conn.fetch("SELECT projection_name, last_position, updated_at FROM projection_checkpoints ORDER BY updated_at DESC")
            events = await conn.fetch(
                "SELECT global_position, stream_id, event_type, recorded_at FROM events ORDER BY global_position DESC LIMIT $1",
                max(10, limit),
            )
            logs: list[dict] = [
                {
                    "id": "ops-outbox-pending",
                    "level": "WARN" if int(outbox_pending or 0) else "INFO",
                    "component": "outbox",
                    "message": f"Pending outbox events: {int(outbox_pending or 0)}",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            ]
            for checkpoint in checkpoints[:10]:
                logs.append(
                    {
                        "id": f"checkpoint-{checkpoint['projection_name']}",
                        "level": "INFO",
                        "component": "projection-daemon",
                        "message": f"{checkpoint['projection_name']} advanced to position {checkpoint['last_position']}",
                        "timestamp": checkpoint["updated_at"].isoformat() if checkpoint["updated_at"] else datetime.utcnow().isoformat(),
                    }
                )
            for event in events[: max(0, limit - len(logs))]:
                logs.append(
                    {
                        "id": f"event-{event['global_position']}",
                        "level": "ERROR" if "Failed" in event["event_type"] else "INFO",
                        "component": event["stream_id"].split("-", 1)[0],
                        "message": f"{event['event_type']} on {event['stream_id']}",
                        "timestamp": event["recorded_at"].isoformat() if event["recorded_at"] else datetime.utcnow().isoformat(),
                    }
                )
            return logs[:limit]
        finally:
            await conn.close()

    @app.post("/api/tools/{tool_name}")
    async def invoke_tool(
        tool_name: str,
        payload: dict,
        request: Request,
        response: Response,
        auth: AuthContext = Depends(resolve_auth_context),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown tool: {tool_name}")
        policy_engine.ensure_tool_allowed(auth, tool_name)
        try:
            await enforce_rate_limit(
                f"rl:{auth.org_id}:{auth.role}:{tool_name}",
                budget=policy_engine.rate_limit_budget(tool_name),
                window_seconds=RATE_LIMIT_WINDOW_SECONDS,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

        if not idempotency_key:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key header is required for browser writes")

        enriched_payload = dict(payload)
        if tool_name == "submit_application":
            enriched_payload.setdefault("tenant_id", auth.org_id)
            enriched_payload.setdefault("owner_user_id", auth.user_id)

        scope = f"{auth.org_id}:{auth.user_id}:{tool_name}:{request.url.path}"
        idem_key = f"idem:{scope}:{idempotency_key}"
        fingerprint = stable_fingerprint(enriched_payload, scope)
        existing = await load_idempotency_record(idem_key)
        if existing:
            if existing.fingerprint != fingerprint:
                response.status_code = status.HTTP_409_CONFLICT
                return {
                    "ok": False,
                    "result": None,
                    "error": {
                        "error_type": "IdempotencyConflict",
                        "message": "The same Idempotency-Key was reused with a different payload.",
                        "context": {"tool_name": tool_name},
                        "suggested_action": "use_a_new_idempotency_key_for_different_payloads",
                    },
                }
            response.headers["X-Idempotent-Replay"] = "true"
            response.status_code = existing.status_code
            return existing.response

        result = await handler(enriched_payload)
        status_code = status.HTTP_200_OK if result.get("ok", True) else status.HTTP_409_CONFLICT
        response.status_code = status_code
        await save_idempotency_record(idem_key, fingerprint, result, status_code, ttl_seconds=IDEMPOTENCY_TTL_SECONDS)

        app_id = enriched_payload.get("application_id") or payload.get("application_id") or payload.get("entity_id")
        org_fragment = auth.org_id or "global"
        patterns = [
            f"cache:{org_fragment}:{auth.user_id}:ledger-health*",
            f"cache:{org_fragment}:{auth.user_id}:applications*",
            f"cache:{org_fragment}:{auth.user_id}:compliance*",
            f"cache:{org_fragment}:{auth.user_id}:audit*",
            f"cache:{org_fragment}:{auth.user_id}:agent-performance*",
        ]
        if app_id:
            patterns.extend(
                [
                    f"cache:{org_fragment}:{auth.user_id}:applications:{app_id}*",
                    f"cache:{org_fragment}:{auth.user_id}:compliance:{app_id}*",
                    f"cache:{org_fragment}:{auth.user_id}:audit:{app_id}*",
                ]
            )
        await invalidate_patterns(*patterns)
        return result

    return app


app = create_app()
