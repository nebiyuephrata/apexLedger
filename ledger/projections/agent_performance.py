"""
ledger/projections/agent_performance.py
=======================================
Aggregated metrics per agent and model version.
"""
from __future__ import annotations
import json


class AgentPerformanceProjection:
    name = "agent_performance"

    async def ensure_tables(self, conn):
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_agent_session_index (
                session_id TEXT PRIMARY KEY,
                agent_type TEXT,
                model_version TEXT,
                application_id TEXT
            );
            CREATE TABLE IF NOT EXISTS projection_agent_performance (
                agent_type TEXT NOT NULL,
                model_version TEXT NOT NULL,
                analyses_completed INT NOT NULL DEFAULT 0,
                total_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                avg_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                total_duration_ms BIGINT NOT NULL DEFAULT 0,
                avg_duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                human_overrides INT NOT NULL DEFAULT 0,
                override_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (agent_type, model_version)
            );
            """
        )

    async def _upsert_metrics(self, conn, agent_type, model_version, delta_conf=0.0, delta_duration=0, inc=0, overrides=0):
        row = await conn.fetchrow(
            "SELECT analyses_completed, total_confidence, total_duration_ms, human_overrides "
            "FROM projection_agent_performance WHERE agent_type=$1 AND model_version=$2",
            agent_type, model_version,
        )
        if row:
            analyses = row["analyses_completed"] + inc
            total_conf = row["total_confidence"] + delta_conf
            total_dur = row["total_duration_ms"] + delta_duration
            total_over = row["human_overrides"] + overrides
        else:
            analyses = inc
            total_conf = delta_conf
            total_dur = delta_duration
            total_over = overrides
        avg_conf = (total_conf / analyses) if analyses else 0.0
        avg_dur = (total_dur / analyses) if analyses else 0.0
        override_rate = (total_over / analyses) if analyses else 0.0
        await conn.execute(
            """
            INSERT INTO projection_agent_performance (
                agent_type, model_version, analyses_completed,
                total_confidence, avg_confidence, total_duration_ms, avg_duration_ms,
                human_overrides, override_rate, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
            ON CONFLICT (agent_type, model_version) DO UPDATE SET
                analyses_completed=EXCLUDED.analyses_completed,
                total_confidence=EXCLUDED.total_confidence,
                avg_confidence=EXCLUDED.avg_confidence,
                total_duration_ms=EXCLUDED.total_duration_ms,
                avg_duration_ms=EXCLUDED.avg_duration_ms,
                human_overrides=EXCLUDED.human_overrides,
                override_rate=EXCLUDED.override_rate,
                updated_at=NOW()
            """,
            agent_type, model_version, analyses,
            total_conf, avg_conf, total_dur, avg_dur, total_over, override_rate,
        )

    async def handle(self, event: dict, conn):
        et = event.get("event_type")
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}

        if et == "AgentSessionStarted":
            await conn.execute(
                "INSERT INTO projection_agent_session_index(session_id, agent_type, model_version, application_id) "
                "VALUES ($1,$2,$3,$4) ON CONFLICT (session_id) DO NOTHING",
                payload.get("session_id"),
                payload.get("agent_type"),
                payload.get("model_version"),
                payload.get("application_id"),
            )
            return

        if et == "AgentSessionCompleted":
            # Duration metrics per agent
            sid = payload.get("session_id")
            row = await conn.fetchrow(
                "SELECT agent_type, model_version FROM projection_agent_session_index WHERE session_id=$1",
                sid,
            )
            if row:
                await self._upsert_metrics(
                    conn,
                    row["agent_type"],
                    row["model_version"],
                    delta_duration=int(payload.get("total_duration_ms", 0)),
                    inc=1,
                )
            return

        if et == "CreditAnalysisCompleted":
            sid = payload.get("session_id")
            decision = payload.get("decision", {}) or {}
            conf = float(decision.get("confidence", 0.0))
            row = await conn.fetchrow(
                "SELECT agent_type, model_version FROM projection_agent_session_index WHERE session_id=$1",
                sid,
            )
            if row:
                await self._upsert_metrics(
                    conn,
                    row["agent_type"],
                    row["model_version"],
                    delta_conf=conf,
                    inc=1,
                    delta_duration=int(payload.get("analysis_duration_ms", 0)),
                )
            return

        if et == "DecisionGenerated":
            sid = payload.get("orchestrator_session_id")
            conf = float(payload.get("confidence", 0.0))
            row = await conn.fetchrow(
                "SELECT agent_type, model_version FROM projection_agent_session_index WHERE session_id=$1",
                sid,
            )
            if row:
                await self._upsert_metrics(
                    conn,
                    row["agent_type"],
                    row["model_version"],
                    delta_conf=conf,
                    inc=1,
                )
            return

        if et == "HumanReviewCompleted":
            if payload.get("override"):
                # Attribute overrides to decision orchestrator (best-effort)
                await self._upsert_metrics(
                    conn,
                    "decision_orchestrator",
                    "unknown",
                    overrides=1,
                    inc=0,
                )

    async def rebuild(self, conn):
        await self.ensure_tables(conn)
        await conn.execute("TRUNCATE projection_agent_performance")
        await conn.execute("TRUNCATE projection_agent_session_index")
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
