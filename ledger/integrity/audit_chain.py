from __future__ import annotations

import hashlib
import json
from typing import Iterable


def _stable_event_repr(event: dict) -> str:
    payload = event.get("payload", {})
    meta = event.get("metadata", {})
    data = {
        "event_id": event.get("event_id"),
        "stream_id": event.get("stream_id"),
        "stream_position": event.get("stream_position"),
        "global_position": event.get("global_position"),
        "event_type": event.get("event_type"),
        "event_version": event.get("event_version"),
        "payload": payload,
        "metadata": meta,
        "recorded_at": event.get("recorded_at"),
    }
    return json.dumps(data, sort_keys=True, default=str)


def compute_chain_hash(events: Iterable[dict], initial_hash: str | None = None) -> str:
    h = initial_hash or ""
    for ev in events:
        msg = h + "|" + _stable_event_repr(ev)
        h = hashlib.sha256(msg.encode("utf-8")).hexdigest()
    return h


def verify_chain(events: Iterable[dict], expected_hash: str, initial_hash: str | None = None) -> bool:
    return compute_chain_hash(events, initial_hash) == expected_hash
