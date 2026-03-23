from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ledger.domain.aggregates.loan_application import LoanApplicationAggregate
from ledger.integrity.audit_chain import compute_chain_hash


def _event_time(event: dict) -> datetime:
    ts = event.get("recorded_at")
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            return datetime.min
    return ts or datetime.min


def _is_app_event(event: dict, application_id: str) -> bool:
    if event.get("stream_id", "").endswith(application_id):
        return True
    payload = event.get("payload") or {}
    return payload.get("application_id") == application_id


async def _collect_events(store, application_id: str) -> list[dict]:
    events = []
    async for e in store.load_all(0):
        if _is_app_event(e, application_id):
            events.append(dict(e))
    events.sort(key=lambda x: x.get("global_position", 0))
    return events


def _narrate(events: list[dict]) -> list[str]:
    lines = []
    for ev in events:
        et = ev.get("event_type")
        p = ev.get("payload") or {}
        if et == "ApplicationSubmitted":
            lines.append(f"Application submitted by {p.get('applicant_id')}")
        elif et == "CreditAnalysisCompleted":
            decision = p.get("decision", {})
            lines.append(f"Credit analysis completed: risk={decision.get('risk_tier')} confidence={decision.get('confidence')}")
        elif et == "FraudScreeningCompleted":
            lines.append(f"Fraud screening completed: score={p.get('fraud_score')}")
        elif et == "ComplianceCheckCompleted":
            lines.append(f"Compliance completed: verdict={p.get('overall_verdict')}")
        elif et == "DecisionGenerated":
            lines.append(f"Decision generated: {p.get('recommendation')} @ {p.get('confidence')}")
        elif et == "ApplicationApproved":
            lines.append("Application approved")
        elif et == "ApplicationDeclined":
            lines.append("Application declined")
    return lines


def _agent_metadata(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        if ev.get("event_type") == "AgentSessionStarted":
            p = ev.get("payload", {})
            out.append({
                "session_id": p.get("session_id"),
                "agent_type": p.get("agent_type"),
                "agent_id": p.get("agent_id"),
                "model_version": p.get("model_version"),
            })
    return out


async def generate_regulatory_package(store, application_id: str, as_of: datetime, out_path: str | None = None) -> dict:
    events = await _collect_events(store, application_id)
    audit_events = [e for e in events if e.get("stream_id", "").startswith("audit-")]

    as_of_events = [e for e in events if _event_time(e) <= as_of]

    loan_events = [e for e in as_of_events if e.get("stream_id", "").startswith("loan-")]
    loan_events.sort(key=lambda x: x.get("global_position", 0))
    agg = LoanApplicationAggregate(application_id=application_id, version=-1)
    for ev in loan_events:
        agg._apply(ev)

    compliance_events = [e for e in as_of_events if e.get("stream_id", "").startswith("compliance-")]
    compliance_state = None
    if compliance_events:
        compliance_events.sort(key=lambda x: x.get("global_position", 0))
        last = compliance_events[-1]
        compliance_state = last.get("payload")

    package = {
        "application_id": application_id,
        "as_of": as_of.isoformat(),
        "events": events,
        "projections": {
            "application_summary": {
                "state": str(agg.state),
                "decision": agg.decision_recommendation,
                "approved_amount_usd": agg.approved_amount_usd,
            },
            "compliance": compliance_state,
        },
        "audit_chain": {
            "integrity_hash": compute_chain_hash(audit_events),
            "events_verified_count": len(audit_events),
        },
        "narrative": _narrate(as_of_events),
        "agent_metadata": _agent_metadata(events),
        "generated_at": datetime.now().isoformat(),
    }

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(package, f, indent=2, default=str)
    return package
