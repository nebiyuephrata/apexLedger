import asyncio, os
from uuid import uuid4
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ledger.event_store import EventStore, OptimisticConcurrencyError

DB_URL = os.environ.get("TEST_DB_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")


def _event(etype):
    return [{"event_type": etype, "event_version": 1, "payload": {"test": True}}]


@pytest.fixture
async def store():
    s = EventStore(DB_URL)
    await s.connect()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_double_decision_occ_collision(store):
    stream_id = f"loan-concurrency-{uuid4().hex[:8]}"
    await store.append(stream_id, _event("Init"), expected_version=-1)

    async def attempt(etype):
        return await store.append(stream_id, _event(etype), expected_version=1)

    results = await asyncio.gather(
        attempt("CreditAnalysisCompleted"),
        attempt("CreditAnalysisCompleted"),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, list)]
    errors = [r for r in results if isinstance(r, OptimisticConcurrencyError)]
    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(errors) == 1
    assert await store.stream_version(stream_id) == 2
