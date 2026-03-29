from __future__ import annotations

from fastapi.testclient import TestClient

from ledger.api import server as api_server
from ledger.api.infra import InMemoryInfraStore


def make_client(monkeypatch):
    monkeypatch.setenv("LEDGER_ALLOW_DEV_AUTH", "true")
    monkeypatch.setattr("ledger.api.infra._store", InMemoryInfraStore(), raising=False)
    return TestClient(api_server.create_app())


def auth_headers(role: str, org_id: str = "org_demo", user_id: str | None = None, internal: bool = False):
    return {
        "X-Ledger-Dev-Role": role,
        "X-Ledger-Dev-Org-Id": org_id,
        "X-Ledger-Dev-User-Id": user_id or f"{role}-user",
        "X-Ledger-Dev-Internal": "true" if internal else "false",
    }


def test_session_capabilities_resolve_from_policy(monkeypatch):
    client = make_client(monkeypatch)
    response = client.get("/api/session", headers=auth_headers("admin", internal=True))
    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "admin"
    assert "admin" in payload["allowed_views"]
    assert "run_integrity_check" in payload["allowed_tools"]


def test_logs_are_restricted(monkeypatch):
    client = make_client(monkeypatch)
    response = client.get("/api/ops/logs", headers=auth_headers("applicant"))
    assert response.status_code == 403
    assert "restricted" in response.json()["detail"].lower()


def test_tool_requires_idempotency_key(monkeypatch):
    client = make_client(monkeypatch)

    async def fake_submit(payload):
        return {"ok": True, "result": payload, "error": None}

    monkeypatch.setitem(api_server.TOOL_HANDLERS, "submit_application", fake_submit)

    response = client.post("/api/tools/submit_application", headers=auth_headers("loan_officer"), json={"application_id": "app-1", "applicant_id": "A-1", "requested_amount_usd": 10, "loan_purpose": "working_capital"})
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]


def test_idempotent_replay_and_conflict(monkeypatch):
    client = make_client(monkeypatch)
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

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["X-Idempotent-Replay"] == "true"
    assert calls["count"] == 1
    assert conflict.status_code == 409
    assert conflict.json()["error"]["error_type"] == "IdempotencyConflict"


def test_cross_tenant_application_denied(monkeypatch):
    client = make_client(monkeypatch)

    async def fake_summary(application_id: str):
        return {
            "application_id": application_id,
            "applicant_id": "A-9",
            "tenant_id": "org_other",
            "owner_user_id": "owner-1",
        }

    monkeypatch.setattr(api_server.mcp_resources, "get_application_summary", fake_summary)
    response = client.get("/api/applications/app-9", headers=auth_headers("loan_officer", org_id="org_demo"))
    assert response.status_code == 403
    assert "cross-tenant" in response.json()["detail"].lower()


def test_health_cache_invalidates_after_tool_write(monkeypatch):
    client = make_client(monkeypatch)
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
    assert first.status_code == 200
    assert second.status_code == 200
    assert counter["value"] == 1

    write = client.post(
        "/api/tools/submit_application",
        headers={**headers, "Idempotency-Key": "idem-456"},
        json={"application_id": "app-cache", "applicant_id": "A-3", "requested_amount_usd": 55, "loan_purpose": "working_capital"},
    )
    assert write.status_code == 200

    third = client.get("/api/ledger/health", headers=headers)
    assert third.status_code == 200
    assert counter["value"] == 2
