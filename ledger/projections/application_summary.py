"""
ledger/projections/application_summary.py
=========================================
Operational dashboard projection: one row per application.
"""
from __future__ import annotations
from datetime import datetime
import json


class ApplicationSummaryProjection:
    name = "application_summary"

    async def ensure_tables(self, conn):
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_application_summary (
                application_id TEXT PRIMARY KEY,
                state TEXT,
                applicant_id TEXT,
                requested_amount_usd NUMERIC(15,2),
                approved_amount_usd NUMERIC(15,2),
                risk_tier TEXT,
                fraud_score DOUBLE PRECISION,
                compliance_status TEXT,
                decision_recommendation TEXT,
                agent_sessions_completed TEXT[],
                human_reviewer_id TEXT,
                final_decision_at TIMESTAMPTZ,
                last_event_type TEXT,
                last_event_at TIMESTAMPTZ
            );
            """
        )
        # Backfill new columns for existing tables (idempotent)
        await conn.execute("ALTER TABLE projection_application_summary ADD COLUMN IF NOT EXISTS approved_amount_usd NUMERIC(15,2)")
        await conn.execute("ALTER TABLE projection_application_summary ADD COLUMN IF NOT EXISTS decision_recommendation TEXT")
        await conn.execute("ALTER TABLE projection_application_summary ADD COLUMN IF NOT EXISTS agent_sessions_completed TEXT[]")
        await conn.execute("ALTER TABLE projection_application_summary ADD COLUMN IF NOT EXISTS human_reviewer_id TEXT")
        await conn.execute("ALTER TABLE projection_application_summary ADD COLUMN IF NOT EXISTS final_decision_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE projection_application_summary ADD COLUMN IF NOT EXISTS last_event_type TEXT")

    def _event_time(self, event: dict) -> datetime:
        ts = event.get("recorded_at")
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts)
            except Exception:
                return datetime.now()
        return ts or datetime.now()

    def _parse_ts(self, value):
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except Exception:
                return None
        return value

    def _state_from_event(self, et: str, payload: dict) -> str | None:
        mapping = {
            "ApplicationSubmitted": "SUBMITTED",
            "DocumentUploadRequested": "DOCUMENTS_PENDING",
            "DocumentUploaded": "DOCUMENTS_UPLOADED",
            "CreditAnalysisRequested": "AWAITING_ANALYSIS",
            "CreditAnalysisCompleted": "ANALYSIS_COMPLETE",
            "FraudScreeningRequested": "FRAUD_SCREENING_REQUESTED",
            "FraudScreeningCompleted": "FRAUD_SCREENING_COMPLETE",
            "ComplianceCheckRequested": "COMPLIANCE_REVIEW",
            "ComplianceCheckCompleted": "COMPLIANCE_COMPLETE",
            "DecisionRequested": "PENDING_DECISION",
            "DecisionGenerated": "PENDING_HUMAN_REVIEW" if (payload.get("recommendation","").upper() in ("REFER","REFERRED")) else "PENDING_DECISION",
            "HumanReviewRequested": "PENDING_HUMAN_REVIEW",
            "ApplicationApproved": "FINAL_APPROVED",
            "ApplicationDeclined": "FINAL_DECLINED",
        }
        return mapping.get(et)

    async def handle(self, event: dict, conn):
        et = event.get("event_type")
        payload = event.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        app_id = payload.get("application_id")
        if not app_id:
            return

        row = await conn.fetchrow(
            "SELECT application_id, state, applicant_id, requested_amount_usd, "
            "approved_amount_usd, risk_tier, fraud_score, compliance_status, "
            "decision_recommendation, agent_sessions_completed, human_reviewer_id, "
            "final_decision_at, last_event_type, last_event_at "
            "FROM projection_application_summary WHERE application_id=$1",
            app_id,
        )
        current = {
            "application_id": app_id,
            "state": None,
            "applicant_id": None,
            "requested_amount_usd": None,
            "approved_amount_usd": None,
            "risk_tier": None,
            "fraud_score": None,
            "compliance_status": None,
            "decision_recommendation": None,
            "agent_sessions_completed": [],
            "human_reviewer_id": None,
            "final_decision_at": None,
            "last_event_type": None,
            "last_event_at": None,
        }
        if row:
            current.update(dict(row))
            if current.get("agent_sessions_completed") is None:
                current["agent_sessions_completed"] = []

        new_state = self._state_from_event(et, payload)
        if new_state:
            current["state"] = new_state

        if et == "ApplicationSubmitted":
            current["applicant_id"] = payload.get("applicant_id")
            current["requested_amount_usd"] = payload.get("requested_amount_usd")
        elif et == "CreditAnalysisCompleted":
            decision = payload.get("decision", {}) or {}
            current["risk_tier"] = decision.get("risk_tier")
        elif et == "FraudScreeningCompleted":
            current["fraud_score"] = payload.get("fraud_score")
        elif et == "ComplianceCheckCompleted":
            current["compliance_status"] = payload.get("overall_verdict")
        elif et == "DecisionGenerated":
            current["decision_recommendation"] = payload.get("recommendation")
            sessions = payload.get("contributing_sessions") or []
            if sessions:
                seen = set(current.get("agent_sessions_completed") or [])
                for sid in sessions:
                    if sid not in seen:
                        seen.add(sid)
                current["agent_sessions_completed"] = list(seen)
        elif et == "HumanReviewCompleted":
            current["human_reviewer_id"] = payload.get("reviewer_id")
            current["decision_recommendation"] = payload.get("final_decision") or current.get("decision_recommendation")
            current["final_decision_at"] = self._parse_ts(payload.get("reviewed_at"))
        elif et == "ApplicationApproved":
            current["approved_amount_usd"] = payload.get("approved_amount_usd")
            current["final_decision_at"] = self._parse_ts(payload.get("approved_at"))
        elif et == "ApplicationDeclined":
            current["final_decision_at"] = self._parse_ts(payload.get("declined_at"))

        current["last_event_type"] = et
        current["last_event_at"] = self._event_time(event)
        if isinstance(current.get("final_decision_at"), str):
            current["final_decision_at"] = self._parse_ts(current.get("final_decision_at"))

        await conn.execute(
            """
            INSERT INTO projection_application_summary (
                application_id, state, applicant_id, requested_amount_usd,
                approved_amount_usd, risk_tier, fraud_score, compliance_status,
                decision_recommendation, agent_sessions_completed, human_reviewer_id,
                final_decision_at, last_event_type, last_event_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (application_id) DO UPDATE SET
                state=EXCLUDED.state,
                applicant_id=EXCLUDED.applicant_id,
                requested_amount_usd=EXCLUDED.requested_amount_usd,
                approved_amount_usd=EXCLUDED.approved_amount_usd,
                risk_tier=EXCLUDED.risk_tier,
                fraud_score=EXCLUDED.fraud_score,
                compliance_status=EXCLUDED.compliance_status,
                decision_recommendation=EXCLUDED.decision_recommendation,
                agent_sessions_completed=EXCLUDED.agent_sessions_completed,
                human_reviewer_id=EXCLUDED.human_reviewer_id,
                final_decision_at=EXCLUDED.final_decision_at,
                last_event_type=EXCLUDED.last_event_type,
                last_event_at=EXCLUDED.last_event_at
            """,
            current["application_id"],
            current["state"],
            current["applicant_id"],
            current["requested_amount_usd"],
            current["approved_amount_usd"],
            current["risk_tier"],
            current["fraud_score"],
            current["compliance_status"],
            current["decision_recommendation"],
            current["agent_sessions_completed"],
            current["human_reviewer_id"],
            current["final_decision_at"],
            current["last_event_type"],
            current["last_event_at"],
        )

    async def rebuild(self, conn):
        await self.ensure_tables(conn)
        await conn.execute("TRUNCATE projection_application_summary")
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
