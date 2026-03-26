from __future__ import annotations

import pytest

from ledger.event_store import InMemoryEventStore


def _event(event_type: str, payload: dict | None = None, event_version: int = 1) -> dict:
    return {
        "event_type": event_type,
        "event_version": event_version,
        "payload": payload or {},
    }


class _StubUpcasters:
    async def upcast(self, event: dict) -> dict:
        updated = dict(event)
        updated["payload"] = {**dict(event.get("payload", {})), "upcasted": True}
        updated["event_version"] = max(int(event.get("event_version", 1)), 2)
        return updated


@pytest.mark.asyncio
async def test_load_all_supports_from_global_position_and_event_type_filters():
    store = InMemoryEventStore()
    await store.append("loan-a", [_event("ApplicationSubmitted", {"application_id": "A"})], expected_version=-1)
    await store.append("credit-a", [_event("CreditAnalysisCompleted", {"application_id": "A"})], expected_version=-1)
    await store.append("loan-b", [_event("ApplicationApproved", {"application_id": "B"})], expected_version=-1)

    results = [
        e async for e in store.load_all(
            from_global_position=1,
            event_types=["CreditAnalysisCompleted", "ApplicationApproved"],
            batch_size=1,
        )
    ]

    assert [e["event_type"] for e in results] == ["CreditAnalysisCompleted", "ApplicationApproved"]
    assert [e["global_position"] for e in results] == [1, 2]


@pytest.mark.asyncio
async def test_load_all_applies_upcasters_transparently():
    store = InMemoryEventStore()
    store.upcasters = _StubUpcasters()
    await store.append("credit-a", [_event("CreditAnalysisCompleted", {"application_id": "A"}, event_version=1)], expected_version=-1)

    events = [e async for e in store.load_all(from_global_position=0)]

    assert len(events) == 1
    assert events[0]["event_version"] == 2
    assert events[0]["payload"]["upcasted"] is True
