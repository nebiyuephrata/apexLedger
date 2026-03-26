"""
ledger/domain/aggregates/audit_ledger.py
========================================
AuditLedger aggregate. Replays integrity-check events to rebuild the
latest known hash-chain status for an entity.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuditLedgerAggregate:
    entity_id: str
    entity_type: str | None = None
    checks_run: int = 0
    last_integrity_hash: str | None = None
    previous_hash: str | None = None
    chain_valid: bool = True
    tamper_detected: bool = False
    last_check_timestamp: str | None = None
    version: int = -1
    events: list[dict] = field(default_factory=list)

    @classmethod
    async def load(cls, store, entity_id: str) -> "AuditLedgerAggregate":
        if entity_id.startswith("audit-"):
            stream_id = entity_id
            eid = entity_id[len("audit-"):]
        else:
            stream_id = f"audit-{entity_id}"
            eid = entity_id
        agg = cls(entity_id=eid)
        events = await store.load_stream(stream_id)
        for event in events:
            agg._apply(event)
        return agg

    def _apply(self, event: dict) -> None:
        handler = getattr(self, f"_on_{event.get('event_type')}", None)
        if handler:
            handler(event.get("payload", {}))
        if "stream_position" in event:
            self.version = event["stream_position"]
        else:
            self.version += 1
        self.events.append(event)

    def _on_AuditIntegrityCheckRun(self, payload: dict) -> None:
        self.entity_type = payload.get("entity_type") or self.entity_type
        self.checks_run += 1
        self.last_integrity_hash = payload.get("integrity_hash")
        self.previous_hash = payload.get("previous_hash")
        self.chain_valid = bool(payload.get("chain_valid", self.chain_valid))
        self.tamper_detected = bool(payload.get("tamper_detected", self.tamper_detected))
        self.last_check_timestamp = payload.get("check_timestamp")
