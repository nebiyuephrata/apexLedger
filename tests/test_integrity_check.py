from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from ledger.event_store import EventStore
from ledger.mcp.tools import run_integrity_check

DB_URL = os.environ.get("TEST_DB_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")


@pytest.mark.asyncio
async def test_run_integrity_check_detects_direct_payload_tamper():
    store = EventStore(DB_URL)
    try:
        await store.connect()
    except Exception:
        pytest.skip("PostgreSQL not available for integrity test")

    app_id = f"APP-INTEG-{uuid4().hex[:6]}"
    old_check = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()

    await store.append(
        f"loan-{app_id}",
        [
            {
                "event_type": "ApplicationSubmitted",
                "event_version": 1,
                "payload": {
                    "application_id": app_id,
                    "applicant_id": "COMP-001",
                    "requested_amount_usd": "100000",
                    "loan_purpose": "working_capital",
                    "loan_term_months": 24,
                    "submission_channel": "web",
                    "contact_email": "tamper@example.com",
                    "contact_name": "Tamper Test",
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                    "application_reference": app_id,
                },
            }
        ],
        expected_version=-1,
    )

    first = await run_integrity_check(
        {
            "entity_id": app_id,
            "entity_type": "application",
            "check_timestamp": old_check,
        }
    )
    assert first["ok"] is True
    assert first["result"]["tamper_detected"] is False

    conn = await asyncpg.connect(DB_URL)
    await conn.execute(
        """
        UPDATE events
        SET payload = jsonb_set(payload, '{contact_name}', '\"Tampered Name\"'::jsonb)
        WHERE stream_id = $1 AND stream_position = 1
        """,
        f"loan-{app_id}",
    )
    await conn.close()

    second = await run_integrity_check(
        {
            "entity_id": app_id,
            "entity_type": "application",
            "check_timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    assert second["ok"] is True
    assert second["result"]["tamper_detected"] is True
    assert second["result"]["chain_valid"] is False

    await store.close()
