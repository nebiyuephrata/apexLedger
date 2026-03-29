from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from time import perf_counter
from typing import Any, Callable, Sequence

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ledger.api.auth import AuthContext, resolve_auth_context
from ledger.api.contracts import ApiEnvelope, ApiError, ApiMeta
from ledger.api.infra import (
    enforce_rate_limit,
    get_cached_or_load,
    invalidate_patterns,
    load_idempotency_record,
    record_action,
    record_db_query,
    record_route_latency,
    request_id_var,
    runtime_snapshot,
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
POOL_MIN_SIZE = int(os.environ.get("LEDGER_DB_POOL_MIN_SIZE", "1"))
POOL_MAX_SIZE = int(os.environ.get("LEDGER_DB_POOL_MAX_SIZE", "8"))

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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.db_pool = await asyncpg.create_pool(DB_URL, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE)
    try:
        yield
    finally:
        await app.state.db_pool.close()


async def _fetch(pool: asyncpg.Pool, query: str, *args: Any):
    started = perf_counter()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    record_db_query(int((perf_counter() - started) * 1000))
    return rows


async def _fetchrow(pool: asyncpg.Pool, query: str, *args: Any):
    started = perf_counter()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
    record_db_query(int((perf_counter() - started) * 1000))
    return row


async def _fetchval(pool: asyncpg.Pool, query: str, *args: Any):
    started = perf_counter()
    async with pool.acquire() as conn:
        value = await conn.fetchval(query, *args)
    record_db_query(int((perf_counter() - started) * 1000))
    return value


async def _resolve_application_record(application_id: str) -> ApplicationAccessRecord | None:
    row = await mcp_resources.get_application_summary(application_id)
    if not row:
        return None
    return ApplicationAccessRecord(
        application_id=application_id,
        applicant_id=row.get("applicant_id"),
        tenant_id=row.get("tenant_id"),
        owner_user_id=row.get("owner_user_id"),
    )


def _detail_payload(detail: Any, request_id: str, default_message: str, default_type: str = "ApiError") -> ApiError:
    if isinstance(detail, dict):
        return ApiError(
            error_type=str(detail.get("error_type") or default_type),
            message=str(detail.get("message") or default_message),
            context=detail.get("context") or {},
            suggested_action=detail.get("suggested_action"),
            request_id=request_id,
        )
    return ApiError(
        error_type=default_type,
        message=str(detail or default_message),
        context={},
        suggested_action=None,
        request_id=request_id,
    )


def _meta(request: Request, *, idempotency_key: str | None = None, idempotent_replay: bool = False) -> ApiMeta:
    latency_ms = None
    started = getattr(request.state, "started_at", None)
    if started is not None:
        latency_ms = int((perf_counter() - started) * 1000)
    return ApiMeta(
        request_id=request.state.request_id,
        idempotency_key=idempotency_key,
        idempotent_replay=idempotent_replay,
        latency_ms=latency_ms,
    )


def _ok(request: Request, result: Any, *, status_code: int = 200, idempotency_key: str | None = None, idempotent_replay: bool = False) -> JSONResponse:
    payload = ApiEnvelope(ok=True, result=result, error=None, meta=_meta(request, idempotency_key=idempotency_key, idempotent_replay=idempotent_replay))
    response = JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))
    if idempotent_replay:
        response.headers["X-Idempotent-Replay"] = "true"
    response.headers["X-Request-Id"] = request.state.request_id
    return response


def _error_response(request: Request, *, status_code: int, error: ApiError, idempotency_key: str | None = None, idempotent_replay: bool = False) -> JSONResponse:
    payload = ApiEnvelope(ok=False, result=None, error=error, meta=_meta(request, idempotency_key=idempotency_key, idempotent_replay=idempotent_replay))
    response = JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))
    response.headers["X-Request-Id"] = request.state.request_id
    if idempotent_replay:
        response.headers["X-Idempotent-Replay"] = "true"
    return response


def _cache_scope(auth: AuthContext, resource: str, *, private: bool = False) -> str:
    visibility = f"user:{auth.user_id}" if private or auth.role == "applicant" else f"role:{auth.role}"
    return f"cache:{auth.org_id or 'global'}:{visibility}:{resource}"


def _paginate(items: Sequence[dict[str, Any]] | list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
    total = len(items)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    return {
        "items": list(items[start:end]),
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": end < total,
    }


async def _tool_payload_for_mutation(auth: AuthContext, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(payload)
    if tool_name == "submit_application":
        enriched.setdefault("tenant_id", auth.org_id)
        enriched.setdefault("owner_user_id", auth.user_id)
        policy_engine.ensure_submit_allowed(auth, enriched.get("tenant_id"), enriched.get("owner_user_id"))
        return enriched

    application_id = (
        enriched.get("application_id")
        or enriched.get("entity_id")
        or enriched.get("loan_application_id")
    )
    if application_id:
        record = await _resolve_application_record(str(application_id))
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown application: {application_id}")
        policy_engine.ensure_application_mutation_allowed(auth, record)
    return enriched


async def _invalidate_after_write(auth: AuthContext, application_id: str | None) -> None:
    org_fragment = auth.org_id or "global"
    patterns = [
        f"cache:{org_fragment}:*:ledger-health*",
        f"cache:{org_fragment}:*:applications*",
        f"cache:{org_fragment}:*:compliance*",
        f"cache:{org_fragment}:*:audit*",
        f"cache:{org_fragment}:*:agent-performance*",
        f"cache:{org_fragment}:*:agent-sessions*",
        f"cache:{org_fragment}:*:ops-logs*",
    ]
    if application_id:
        patterns.extend(
            [
                f"cache:{org_fragment}:*:applications:{application_id}*",
                f"cache:{org_fragment}:*:compliance:{application_id}*",
                f"cache:{org_fragment}:*:audit:{application_id}*",
            ]
        )
    await invalidate_patterns(*patterns)


def create_app() -> FastAPI:
    app = FastAPI(title="The Ledger BFF", version="0.2.0", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in os.environ.get("LEDGER_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id", "X-Ledger-Dev-Role", "X-Ledger-Dev-Org-Id", "X-Ledger-Dev-User-Id", "X-Ledger-Dev-Internal", "X-Ledger-Dev-Email", "X-Ledger-Dev-Name"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.started_at = perf_counter()
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = request_id
        record_route_latency(f"{request.method} {request.url.path}", int((perf_counter() - request.state.started_at) * 1000))
        return response

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        error = _detail_payload(exc.detail, request.state.request_id, "Request failed")
        return _error_response(request, status_code=exc.status_code, error=error)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        error = ApiError(
            error_type=type(exc).__name__,
            message="The browser API failed unexpectedly.",
            context={"detail": str(exc)},
            suggested_action="retry_or_contact_support_with_request_id",
            request_id=request.state.request_id,
        )
        return _error_response(request, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error=error)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "ledger-bff"}

    @app.get("/api/session")
    async def session(request: Request, auth: AuthContext = Depends(resolve_auth_context)) -> JSONResponse:
        payload = policy_engine.session_payload(auth)
        payload["support_flags"] = {
            "dev_auth_enabled": os.environ.get("LEDGER_ALLOW_DEV_AUTH", "false").lower() == "true" if auth.role in {"admin", "security_officer"} else False,
        }
        return _ok(request, payload)

    @app.get("/api/meta/actors")
    async def actor_profiles(request: Request, auth: AuthContext = Depends(resolve_auth_context)) -> JSONResponse:
        _ = auth
        return _ok(request, policy_engine.actor_profiles())

    @app.get("/api/meta/commands")
    async def command_catalog(request: Request, auth: AuthContext = Depends(resolve_auth_context)) -> JSONResponse:
        allowed = set(policy_engine.session_payload(auth)["allowed_tools"])
        return _ok(request, [item for item in TOOL_DEFINITIONS if item["name"] in allowed])

    @app.get("/api/applications")
    async def applications(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=100),
        search: str | None = Query(default=None),
        state: str | None = Query(default=None),
        auth: AuthContext = Depends(resolve_auth_context),
    ) -> JSONResponse:
        policy_engine.ensure_view_allowed(auth, "applicant" if auth.role == "applicant" else "dashboard")
        cache_key = _cache_scope(auth, f"applications:{page}:{page_size}:{search or 'all'}:{state or 'all'}", private=auth.role == "applicant")

        async def _load() -> dict[str, Any]:
            rows = await _fetch(
                request.app.state.db_pool,
                "SELECT application_id, state, applicant_id, tenant_id, owner_user_id, requested_amount_usd, approved_amount_usd, risk_tier, fraud_score, compliance_status, decision_recommendation, agent_sessions_completed, human_reviewer_id, final_decision_at, last_event_type, last_event_at "
                "FROM projection_application_summary ORDER BY last_event_at DESC NULLS LAST, application_id ASC",
            )
            filtered = policy_engine.filter_applications(auth, [dict(row) for row in rows])
            if search:
                needle = search.lower()
                filtered = [item for item in filtered if needle in str(item.get("application_id", "")).lower() or needle in str(item.get("applicant_id", "")).lower()]
            if state:
                filtered = [item for item in filtered if str(item.get("state") or "").upper() == state.upper()]
            return _paginate(filtered, page, page_size)

        return _ok(request, await get_cached_or_load(cache_key, CACHE_TTL_APPLICATIONS, _load))

    @app.get("/api/applications/{application_id}")
    async def application_summary(application_id: str, request: Request, auth: AuthContext = Depends(resolve_auth_context)) -> JSONResponse:
        cache_key = _cache_scope(auth, f"applications:{application_id}", private=True)

        async def _load() -> dict | None:
            row = await _fetchrow(
                request.app.state.db_pool,
                "SELECT * FROM projection_application_summary WHERE application_id=$1",
                application_id,
            )
            if not row:
                return None
            row = dict(row)
            record = ApplicationAccessRecord(
                application_id=application_id,
                applicant_id=row.get("applicant_id"),
                tenant_id=row.get("tenant_id"),
                owner_user_id=row.get("owner_user_id"),
            )
            policy_engine.ensure_application_visible(auth, record)
            return row

        return _ok(request, await get_cached_or_load(cache_key, CACHE_TTL_APPLICATIONS, _load))

    @app.get("/api/applications/{application_id}/compliance")
    async def compliance_view(application_id: str, request: Request, as_of: str | None = None, auth: AuthContext = Depends(resolve_auth_context)) -> JSONResponse:
        policy_engine.ensure_view_allowed(auth, "compliance")
        summary_row = await _fetchrow(
            request.app.state.db_pool,
            "SELECT applicant_id, tenant_id, owner_user_id FROM projection_application_summary WHERE application_id=$1",
            application_id,
        )
        summary = dict(summary_row) if summary_row else None
        if not summary:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown application: {application_id}")
        record = ApplicationAccessRecord(
            application_id=application_id,
            applicant_id=summary.get("applicant_id"),
            tenant_id=summary.get("tenant_id"),
            owner_user_id=summary.get("owner_user_id"),
        )
        policy_engine.ensure_application_visible(auth, record)
        cache_key = _cache_scope(auth, f"compliance:{application_id}:{as_of or 'current'}", private=auth.role in {"applicant", "loan_officer"})
        async def _load() -> dict | None:
            if as_of:
                row = await _fetchrow(
                    request.app.state.db_pool,
                    "SELECT state FROM projection_compliance_snapshots WHERE application_id=$1 AND as_of <= $2 ORDER BY as_of DESC LIMIT 1",
                    application_id,
                    datetime.fromisoformat(as_of),
                )
                if not row:
                    return None
                state = row["state"]
                if isinstance(state, str):
                    return json.loads(state)
                return state if isinstance(state, dict) else None
            row = await _fetchrow(
                request.app.state.db_pool,
                "SELECT * FROM projection_compliance_audit WHERE application_id=$1 ORDER BY recorded_at DESC LIMIT 1",
                application_id,
            )
            return dict(row) if row else None

        result = await get_cached_or_load(cache_key, CACHE_TTL_COMPLIANCE, _load)
        return _ok(request, result)

    @app.get("/api/applications/{application_id}/audit-trail")
    async def audit_trail(
        application_id: str,
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        event_type: str | None = Query(default=None),
        auth: AuthContext = Depends(resolve_auth_context),
    ) -> JSONResponse:
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
        cache_key = _cache_scope(auth, f"audit:{application_id}:{page}:{page_size}:{event_type or 'all'}")

        async def _load() -> dict[str, Any]:
            items = await mcp_resources.get_audit_trail(application_id)
            if event_type:
                items = [item for item in items if str(item.get("event_type") or "") == event_type]
            return _paginate(items, page, page_size)

        return _ok(request, await get_cached_or_load(cache_key, CACHE_TTL_AUDIT, _load))

    @app.get("/api/agents/performance")
    async def agent_performance(request: Request, agent_type: str | None = None, auth: AuthContext = Depends(resolve_auth_context)) -> JSONResponse:
        policy_engine.ensure_view_allowed(auth, "admin")
        agent_id = agent_type or "decision_orchestrator"
        cache_key = _cache_scope(auth, f"agent-performance:{agent_id}")
        async def _load() -> list[dict[str, Any]]:
            rows = await _fetch(
                request.app.state.db_pool,
                "SELECT * FROM projection_agent_performance WHERE agent_type=$1",
                agent_id,
            )
            return [dict(row) for row in rows]

        return _ok(request, await get_cached_or_load(cache_key, CACHE_TTL_APPLICATIONS, _load))

    @app.get("/api/agents/sessions")
    async def agent_sessions(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=100),
        agent_type: str | None = Query(default=None),
        auth: AuthContext = Depends(resolve_auth_context),
    ) -> JSONResponse:
        policy_engine.ensure_view_allowed(auth, "dashboard")
        cache_key = _cache_scope(auth, f"agent-sessions:{page}:{page_size}:{agent_type or 'all'}")

        async def _load() -> dict[str, Any]:
            query = (
                "SELECT s.session_id, s.agent_type, s.application_id, s.model_version, "
                "a.applicant_id, a.tenant_id, a.owner_user_id, a.last_event_type "
                "FROM projection_agent_session_index s "
                "LEFT JOIN projection_application_summary a ON a.application_id = s.application_id "
                "WHERE ($1::text IS NULL OR s.agent_type = $1) "
                "ORDER BY s.session_id DESC"
            )
            rows = await _fetch(request.app.state.db_pool, query, agent_type)
            items: list[dict[str, Any]] = []
            for row in rows:
                app_id = row["application_id"]
                if app_id:
                    record = ApplicationAccessRecord(
                        application_id=app_id,
                        applicant_id=row.get("applicant_id"),
                        tenant_id=row.get("tenant_id"),
                        owner_user_id=row.get("owner_user_id"),
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
                        "last_node": row.get("last_event_type") or "reconstruct_from_stream",
                        "status": "running",
                        "context_source": row["model_version"],
                    }
                )
            return _paginate(items, page, page_size)

        return _ok(request, await get_cached_or_load(cache_key, CACHE_TTL_APPLICATIONS, _load))

    @app.get("/api/ledger/health")
    async def ledger_health(request: Request, auth: AuthContext = Depends(resolve_auth_context)) -> JSONResponse:
        cache_key = _cache_scope(auth, "ledger-health")
        health_payload = await get_cached_or_load(cache_key, CACHE_TTL_HEALTH, mcp_resources.get_health)
        return _ok(request, health_payload)

    @app.get("/api/ops/logs")
    async def ops_logs(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        level: str | None = Query(default=None),
        search: str | None = Query(default=None),
        auth: AuthContext = Depends(resolve_auth_context),
    ) -> JSONResponse:
        policy_engine.ensure_logs_allowed(auth)
        cache_key = _cache_scope(auth, f"ops-logs:{page}:{page_size}:{level or 'all'}:{search or 'all'}")

        async def _load() -> dict[str, Any]:
            outbox_pending = await _fetchval(request.app.state.db_pool, "SELECT COUNT(*) FROM outbox WHERE published_at IS NULL")
            checkpoints = await _fetch(request.app.state.db_pool, "SELECT projection_name, last_position, updated_at FROM projection_checkpoints ORDER BY updated_at DESC")
            events = await _fetch(
                request.app.state.db_pool,
                "SELECT global_position, stream_id, event_type, recorded_at FROM events ORDER BY global_position DESC LIMIT $1",
                max(50, page_size * 3),
            )
            logs: list[dict[str, Any]] = [
                {
                    "id": "ops-outbox-pending",
                    "level": "WARN" if int(outbox_pending or 0) else "INFO",
                    "component": "outbox",
                    "message": f"Pending outbox events: {int(outbox_pending or 0)}",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            ]
            logs.extend(
                {
                    "id": f"checkpoint-{checkpoint['projection_name']}",
                    "level": "INFO",
                    "component": "projection-daemon",
                    "message": f"{checkpoint['projection_name']} advanced to position {checkpoint['last_position']}",
                    "timestamp": checkpoint["updated_at"].isoformat() if checkpoint["updated_at"] else datetime.utcnow().isoformat(),
                }
                for checkpoint in checkpoints[:10]
            )
            logs.extend(
                {
                    "id": f"event-{event['global_position']}",
                    "level": "ERROR" if "Failed" in event["event_type"] else "INFO",
                    "component": event["stream_id"].split("-", 1)[0],
                    "message": f"{event['event_type']} on {event['stream_id']}",
                    "timestamp": event["recorded_at"].isoformat() if event["recorded_at"] else datetime.utcnow().isoformat(),
                }
                for event in events
            )
            logs.extend(runtime_snapshot()["recent_actions"])
            if level:
                logs = [item for item in logs if str(item.get("level") or "").upper() == level.upper()]
            if search:
                needle = search.lower()
                logs = [item for item in logs if needle in str(item.get("message") or "").lower() or needle in str(item.get("component") or "").lower()]
            logs.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
            return _paginate(logs, page, page_size)

        return _ok(request, await get_cached_or_load(cache_key, 3, _load))

    @app.get("/api/ops/runtime")
    async def ops_runtime(request: Request, auth: AuthContext = Depends(resolve_auth_context)) -> JSONResponse:
        policy_engine.ensure_logs_allowed(auth)
        return _ok(request, runtime_snapshot())

    @app.post("/api/tools/{tool_name}")
    async def invoke_tool(
        tool_name: str,
        payload: dict,
        request: Request,
        auth: AuthContext = Depends(resolve_auth_context),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={
                "error_type": "UnknownTool",
                "message": f"Unknown tool: {tool_name}",
                "context": {"tool_name": tool_name},
                "suggested_action": "use_api_meta_commands_to_discover_supported_tools",
            })
        policy_engine.ensure_tool_allowed(auth, tool_name)
        try:
            await enforce_rate_limit(
                f"rl:{auth.org_id}:{auth.role}:{tool_name}",
                budget=policy_engine.rate_limit_budget(tool_name),
                window_seconds=RATE_LIMIT_WINDOW_SECONDS,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail={
                "error_type": "RateLimitExceeded",
                "message": str(exc),
                "context": {"tool_name": tool_name, "org_id": auth.org_id, "role": auth.role},
                "suggested_action": "wait_for_the_rate_limit_window_then_retry",
            }) from exc

        if not idempotency_key:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
                "error_type": "MissingIdempotencyKey",
                "message": "Idempotency-Key header is required for browser writes",
                "context": {"tool_name": tool_name},
                "suggested_action": "generate_a_new_uuid_and_retry_the_command",
            })

        enriched_payload = await _tool_payload_for_mutation(auth, tool_name, payload)
        scope = f"{auth.org_id}:{auth.user_id}:{tool_name}:{request.url.path}"
        idem_key = f"idem:{scope}:{idempotency_key}"
        fingerprint = stable_fingerprint(enriched_payload, scope)
        existing = await load_idempotency_record(idem_key)
        if existing:
            if existing.fingerprint != fingerprint:
                return _error_response(
                    request,
                    status_code=status.HTTP_409_CONFLICT,
                    idempotency_key=idempotency_key,
                    error=ApiError(
                        error_type="IdempotencyConflict",
                        message="The same Idempotency-Key was reused with a different payload.",
                        context={"tool_name": tool_name},
                        suggested_action="use_a_new_idempotency_key_for_different_payloads",
                        request_id=request.state.request_id,
                    ),
                )
            if existing.response.get("ok", True):
                return _ok(
                    request,
                    existing.response.get("result"),
                    status_code=existing.status_code,
                    idempotency_key=idempotency_key,
                    idempotent_replay=True,
                )
            return _error_response(
                request,
                status_code=existing.status_code,
                idempotency_key=idempotency_key,
                idempotent_replay=True,
                error=ApiError.model_validate(existing.response.get("error") or {}),
            )

        started = perf_counter()
        handler_result = await handler(enriched_payload)
        latency_ms = int((perf_counter() - started) * 1000)
        response_error = handler_result.get("error")
        status_code = status.HTTP_200_OK if handler_result.get("ok", True) else status.HTTP_409_CONFLICT
        envelope_dict = ApiEnvelope(
            ok=bool(handler_result.get("ok", True)),
            result=handler_result.get("result"),
            error=_detail_payload(response_error, request.state.request_id, "Command failed", default_type="CommandError") if response_error else None,
            meta=ApiMeta(
                request_id=request.state.request_id,
                idempotency_key=idempotency_key,
                idempotent_replay=False,
                latency_ms=latency_ms,
            ),
        ).model_dump(mode="json")
        await save_idempotency_record(idem_key, fingerprint, envelope_dict, status_code, ttl_seconds=IDEMPOTENCY_TTL_SECONDS)

        application_id = (
            enriched_payload.get("application_id")
            or payload.get("application_id")
            or payload.get("entity_id")
        )
        await _invalidate_after_write(auth, str(application_id) if application_id else None)
        record_action(
            {
                "id": f"browser-action-{request.state.request_id}",
                "level": "INFO" if status_code < 400 else "ERROR",
                "component": "browser-api",
                "message": f"{tool_name} completed with status {status_code}",
                "timestamp": datetime.utcnow().isoformat(),
                "actor": auth.role,
                "org_id": auth.org_id,
                "user_id": auth.user_id,
                "session_mode": "service" if auth.identity_type == "service" else "interactive",
                "request_id": request.state.request_id,
                "idempotency_key": idempotency_key,
                "latency_ms": latency_ms,
                "result": "ok" if status_code < 400 else "error",
            }
        )

        if envelope_dict["ok"]:
            return _ok(request, envelope_dict["result"], status_code=status_code, idempotency_key=idempotency_key)
        return _error_response(
            request,
            status_code=status_code,
            idempotency_key=idempotency_key,
            error=ApiError.model_validate(envelope_dict["error"]),
        )

    return app


app = create_app()
