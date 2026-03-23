import os
from datetime import datetime
from uuid import uuid4
import asyncpg
import pytest

from ledger.event_store import EventStore
from ledger import upcasters

DB_URL = os.environ.get("TEST_DB_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")


@pytest.mark.asyncio
async def test_upcaster_does_not_mutate_db_payload():
    store = EventStore(DB_URL, upcaster_registry=upcasters.registry)
    upcasters.registry.store = store
    try:
        await store.connect()
    except Exception:
        pytest.skip("PostgreSQL not available for upcaster test")

    stream_id = f"credit-upcast-immu-{uuid4().hex[:6]}"
    await store.append(
        stream_id,
        [{
            "event_type": "CreditAnalysisCompleted",
            "event_version": 1,
            "payload": {
                "application_id": "APP-IMM-1",
                "session_id": "sess-1",
                "decision": {
                    "risk_tier": "LOW",
                    "recommended_limit_usd": "10000",
                    "confidence": 0.75,
                    "rationale": "ok",
                },
                "model_deployment_id": "d1",
                "input_data_hash": "h1",
                "analysis_duration_ms": 10,
                "completed_at": datetime.now().isoformat(),
            },
        }],
        expected_version=-1,
    )

    conn = await asyncpg.connect(DB_URL)
    row = await conn.fetchrow(
        "SELECT payload, event_version FROM events WHERE stream_id=$1 ORDER BY stream_position DESC LIMIT 1",
        stream_id,
    )
    await conn.close()

    raw_payload = row["payload"]
    assert "model_version" not in raw_payload
    assert "confidence_score" not in raw_payload
    assert row["event_version"] == 1

    events = await store.load_stream(stream_id)
    event = events[-1]
    assert event["event_version"] == 2
    assert "model_version" in event["payload"]
    assert "confidence_score" in event["payload"]

    conn = await asyncpg.connect(DB_URL)
    row2 = await conn.fetchrow(
        "SELECT payload, event_version FROM events WHERE stream_id=$1 ORDER BY stream_position DESC LIMIT 1",
        stream_id,
    )
    await conn.close()

    raw_payload2 = row2["payload"]
    assert "model_version" not in raw_payload2
    assert "confidence_score" not in raw_payload2
    assert row2["event_version"] == 1

    await store.close()
