from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from ledger.api import server as api_server
from ledger.api.infra import InMemoryInfraStore


class _DummyConn:
    async def fetch(self, query, *args):
        return []

    async def fetchrow(self, query, *args):
        return None

    async def fetchval(self, query, *args):
        return 0


class _DummyAcquire:
    async def __aenter__(self):
        return _DummyConn()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _DummyPool:
    def acquire(self):
        return _DummyAcquire()

    async def close(self):
        return None


def make_client(monkeypatch, allow_dev_auth: bool = True):
    monkeypatch.setenv("LEDGER_ALLOW_DEV_AUTH", "true" if allow_dev_auth else "false")
    monkeypatch.setattr("ledger.api.infra._store", InMemoryInfraStore(), raising=False)

    async def fake_create_pool(*args, **kwargs):
        return _DummyPool()

    monkeypatch.setattr(api_server.asyncpg, "create_pool", fake_create_pool)
    return TestClient(api_server.create_app())


def auth_headers(role: str, org_id: str = "org_demo", user_id: str | None = None, internal: bool = False):
    return {
        "X-Ledger-Dev-Role": role,
        "X-Ledger-Dev-Org-Id": org_id,
        "X-Ledger-Dev-User-Id": user_id or f"{role}-user",
        "X-Ledger-Dev-Internal": "true" if internal else "false",
    }


def unwrap_ok(response):
    assert response.status_code < 400, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["meta"]["request_id"]
    return payload["result"]


def unwrap_error(response, expected_status: int):
    assert response.status_code == expected_status, response.text
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["request_id"]
    return payload["error"]


def test_prod_defaults_reject_dev_auth(monkeypatch):
    with make_client(monkeypatch, allow_dev_auth=False) as client:
        response = client.get("/api/session", headers=auth_headers("admin"))
    error = unwrap_error(response, 401)
    assert "bearer token" in error["message"].lower()


def test_session_capabilities_resolve_from_policy(monkeypatch):
    with make_client(monkeypatch) as client:
        response = client.get("/api/session", headers=auth_headers("admin", internal=True))
    payload = unwrap_ok(response)
    assert payload["role"] == "admin"
    assert "admin" in payload["allowed_views"]
    assert "run_integrity_check" in payload["allowed_tools"]
    assert payload["session_mode"] in {"interactive", "service"}


def test_logs_are_restricted(monkeypatch):
    with make_client(monkeypatch) as client:
        response = client.get("/api/ops/logs", headers=auth_headers("applicant"))
    error = unwrap_error(response, 403)
    assert "restricted" in error["message"].lower()


def test_tool_requires_idempotency_key(monkeypatch):
    with make_client(monkeypatch) as client:

        async def fake_submit(payload):
            return {"ok": True, "result": payload, "error": None}

        monkeypatch.setitem(api_server.TOOL_HANDLERS, "submit_application", fake_submit)

        response = client.post(
            "/api/tools/submit_application",
            headers=auth_headers("loan_officer"),
            json={"application_id": "app-1", "applicant_id": "A-1", "requested_amount_usd": 10, "loan_purpose": "working_capital"},
        )
    error = unwrap_error(response, 400)
    assert error["error_type"] == "MissingIdempotencyKey"


def test_idempotent_replay_and_conflict(monkeypatch):
    with make_client(monkeypatch) as client:
        calls = {"count": 0}

        async def fake_submit(payload):
            calls["count"] += 1
            return {"ok": True, "result": {"application_id": payload["application_id"]}, "error": None}

        monkeypatch.setitem(api_server.TOOL_HANDLERS, "submit_application", fake_submit)
        headers = {
            **auth_headers("loan_officer"),
            "Idempotency-Key": "idem-123",
        }
        body = {"application_id": "app-2", "applicant_id": "A-2", "requested_amount_usd": 25, "loan_purpose": "working_capital"}

        first = client.post("/api/tools/submit_application", headers=headers, json=body)
        second = client.post("/api/tools/submit_application", headers=headers, json=body)
        conflict = client.post(
            "/api/tools/submit_application",
            headers=headers,
            json={**body, "requested_amount_usd": 30},
        )

    assert unwrap_ok(first)["application_id"] == "app-2"
    assert unwrap_ok(second)["application_id"] == "app-2"
    assert second.headers["X-Idempotent-Replay"] == "true"
    assert calls["count"] == 1
    error = unwrap_error(conflict, 409)
    assert error["error_type"] == "IdempotencyConflict"


def test_cross_tenant_application_denied(monkeypatch):
    with make_client(monkeypatch) as client:
        async def fake_fetchrow(pool, query, *args):
            return {
                "application_id": args[0],
                "applicant_id": "A-9",
                "tenant_id": "org_other",
                "owner_user_id": "owner-1",
            }

        monkeypatch.setattr(api_server, "_fetchrow", fake_fetchrow)
        response = client.get("/api/applications/app-9", headers=auth_headers("loan_officer", org_id="org_demo"))

    error = unwrap_error(response, 403)
    assert "cross-tenant" in error["message"].lower()


def test_health_cache_invalidates_after_tool_write(monkeypatch):
    with make_client(monkeypatch) as client:
        counter = {"value": 0}

        async def fake_health():
            counter["value"] += 1
            return {
                "lags": {
                    "application_summary": {
                        "lag_ms": counter["value"],
                        "last_processed_position": 1,
                        "latest_position": 1,
                        "position_delta": 0,
                    }
                }
            }

        async def fake_submit(payload):
            return {"ok": True, "result": {"application_id": payload["application_id"]}, "error": None}

        monkeypatch.setattr(api_server.mcp_resources, "get_health", fake_health)
        monkeypatch.setitem(api_server.TOOL_HANDLERS, "submit_application", fake_submit)

        headers = auth_headers("loan_officer")
        first = client.get("/api/ledger/health", headers=headers)
        second = client.get("/api/ledger/health", headers=headers)
        assert unwrap_ok(first)["lags"]["application_summary"]["lag_ms"] == 1
        assert unwrap_ok(second)["lags"]["application_summary"]["lag_ms"] == 1
        assert counter["value"] == 1

        write = client.post(
            "/api/tools/submit_application",
            headers={**headers, "Idempotency-Key": "idem-456"},
            json={"application_id": "app-cache", "applicant_id": "A-3", "requested_amount_usd": 55, "loan_purpose": "working_capital"},
        )
        assert unwrap_ok(write)["application_id"] == "app-cache"

        third = client.get("/api/ledger/health", headers=headers)
        assert unwrap_ok(third)["lags"]["application_summary"]["lag_ms"] == 2
        assert counter["value"] == 2


def test_applications_are_paginated(monkeypatch):
    with make_client(monkeypatch) as client:
        pool_rows = [
            {
                "application_id": f"app-{index}",
                "state": "SUBMITTED",
                "applicant_id": f"A-{index}",
                "tenant_id": "org_demo",
                "owner_user_id": "loan_officer-user",
                "requested_amount_usd": 1000,
                "approved_amount_usd": None,
                "risk_tier": None,
                "fraud_score": None,
                "compliance_status": None,
                "decision_recommendation": None,
                "agent_sessions_completed": [],
                "human_reviewer_id": None,
                "final_decision_at": None,
                "last_event_type": "ApplicationSubmitted",
                "last_event_at": None,
            }
            for index in range(4)
        ]

        async def fake_fetch(pool, query, *args):
            return pool_rows

        monkeypatch.setattr(api_server, "_fetch", fake_fetch)
        response = client.get("/api/applications?page=1&page_size=2", headers=auth_headers("loan_officer"))

    payload = unwrap_ok(response)
    assert payload["page_size"] == 2
    assert payload["total"] == 4
    assert len(payload["items"]) == 2
