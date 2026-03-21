"""
ledger/projections/compliance_audit.py
======================================
Compliance audit projection with temporal querying (time travel).
"""
from __future__ import annotations
from datetime import datetime
import json


class ComplianceAuditProjection:
    name = "compliance_audit"

    async def ensure_tables(self, conn):
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_compliance_audit (
                id BIGSERIAL PRIMARY KEY,
                application_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                rule_id TEXT,
                rule_name TEXT,
                is_hard_block BOOLEAN,
                overall_verdict TEXT,
                recorded_at TIMESTAMPTZ,
                global_position BIGINT,
                details JSONB
            );
            CREATE INDEX IF NOT EXISTS idx_proj_comp_app ON projection_compliance_audit(application_id);

            CREATE TABLE IF NOT EXISTS projection_compliance_snapshots (
                application_id TEXT NOT NULL,
                as_of TIMESTAMPTZ NOT NULL,
                global_position BIGINT NOT NULL,
                state JSONB NOT NULL,
                PRIMARY KEY (application_id, as_of)
            );
            """
        )

    def _event_time(self, payload: dict) -> datetime:
        for key in ("completed_at", "evaluated_at", "initiated_at"):
            if payload.get(key):
                val = payload.get(key)
                if isinstance(val, str):
                    try:
                        return datetime.fromisoformat(val)
                    except Exception:
                        return datetime.now()
                return val
        return datetime.now()

    async def handle(self, event: dict, conn):
        et = event.get("event_type")
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if not et.startswith("Compliance"):
            return

        app_id = payload.get("application_id")
        if not app_id:
            return

        await conn.execute(
            """
            INSERT INTO projection_compliance_audit (
                application_id, event_type, rule_id, rule_name, is_hard_block,
                overall_verdict, recorded_at, global_position, details
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            app_id,
            et,
            payload.get("rule_id"),
            payload.get("rule_name"),
            payload.get("is_hard_block"),
            payload.get("overall_verdict"),
            datetime.fromisoformat(event.get("recorded_at")) if isinstance(event.get("recorded_at"), str) else event.get("recorded_at"),
            event.get("global_position"),
            json.dumps(payload),
        )

        # Snapshot state (time travel)
        as_of = self._event_time(payload)
        state = {
            "event_type": et,
            "overall_verdict": payload.get("overall_verdict"),
            "has_hard_block": payload.get("has_hard_block") or payload.get("is_hard_block"),
            "rules_passed": payload.get("rules_passed"),
            "rules_failed": payload.get("rules_failed"),
            "rules_noted": payload.get("rules_noted"),
            "rule_id": payload.get("rule_id"),
            "rule_name": payload.get("rule_name"),
        }
        await conn.execute(
            """
            INSERT INTO projection_compliance_snapshots (
                application_id, as_of, global_position, state
            ) VALUES ($1,$2,$3,$4)
            ON CONFLICT (application_id, as_of) DO UPDATE SET
                global_position=EXCLUDED.global_position,
                state=EXCLUDED.state
            """,
            app_id, as_of, event.get("global_position"), json.dumps(state),
        )

    async def get_compliance_at(self, conn, application_id: str, timestamp) -> dict | None:
        row = await conn.fetchrow(
            "SELECT state FROM projection_compliance_snapshots "
            "WHERE application_id=$1 AND as_of <= $2 "
            "ORDER BY as_of DESC LIMIT 1",
            application_id, timestamp,
        )
        if not row:
            return None
        state = row["state"]
        if isinstance(state, str):
            try:
                return json.loads(state)
            except Exception:
                return None
        return state

    async def rebuild(self, conn):
        await self.ensure_tables(conn)
        await conn.execute("TRUNCATE projection_compliance_audit")
        await conn.execute("TRUNCATE projection_compliance_snapshots")
        rows = await conn.fetch(
            "SELECT global_position, stream_id, stream_position, event_type, "
            "event_version, payload, metadata, recorded_at "
            "FROM events ORDER BY global_position ASC"
        )
        for row in rows:
            e = dict(row)
            if isinstance(e.get("payload"), str):
                try:
                    e["payload"] = json.loads(e["payload"])
                except Exception:
                    e["payload"] = {}
            await self.handle(e, conn)
