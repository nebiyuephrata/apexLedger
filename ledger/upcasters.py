"""
ledger/upcasters.py - UpcasterRegistry
======================================
Upcasters transform older event versions to the current schema on read.
They never write to the database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable

from ledger.schema.events import AgentType


class UpcasterRegistry:
    def __init__(self, store=None):
        self.store = store
        self._upcasters: dict[str, dict[int, callable]] = {}

    def upcaster(self, event_type: str, from_version: int, to_version: int):
        def decorator(fn):
            self._upcasters.setdefault(event_type, {})[from_version] = fn
            return fn
        return decorator

    async def upcast(self, event: dict) -> dict:
        et = event.get("event_type")
        v = event.get("event_version", 1)
        chain = self._upcasters.get(et, {})
        while v in chain:
            fn = chain[v]
            payload = dict(event.get("payload", {}))
            result = fn(event, payload)
            if isinstance(result, Awaitable):
                payload = await result
            else:
                payload = result
            v += 1
            event = dict(event)
            event["event_version"] = v
            event["payload"] = payload
        return event

    def _infer_model_version(self, recorded_at: Any) -> str:
        ts = recorded_at
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except Exception:
                ts = None
        if isinstance(ts, datetime):
            cutoff = datetime(2026, 1, 1, tzinfo=ts.tzinfo)
            if ts < cutoff:
                return "legacy-pre-2026"
            return "legacy-2026"
        return "legacy-unknown"

    async def _infer_model_versions(self, payload: dict) -> dict[str, str]:
        if not self.store:
            return {}
        sessions = payload.get("contributing_sessions") or []
        model_versions: dict[str, str] = {}
        for sid in sessions:
            found = False
            for at in AgentType:
                stream_id = f"agent-{at.value}-{sid}"
                events = await self.store.load_stream(stream_id)
                if not events:
                    continue
                first = events[0]
                mv = (first.get("payload", {}) or {}).get("model_version")
                if mv:
                    model_versions[at.value] = mv
                found = True
                break
            if not found:
                model_versions.setdefault("unknown", "unknown")
        return model_versions


registry = UpcasterRegistry()


@registry.upcaster("CreditAnalysisCompleted", from_version=1, to_version=2)
def upcast_credit_v1_v2(event: dict, payload: dict) -> dict:
    payload.setdefault("model_version", registry._infer_model_version(event.get("recorded_at")))
    if "confidence_score" not in payload:
        decision = payload.get("decision", {}) or {}
        payload["confidence_score"] = decision.get("confidence")
    payload.setdefault("regulatory_basis", [])
    return payload


@registry.upcaster("DecisionGenerated", from_version=1, to_version=2)
async def upcast_decision_v1_v2(event: dict, payload: dict) -> dict:
    if "model_versions" not in payload:
        payload["model_versions"] = await registry._infer_model_versions(payload)
    return payload
