from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from ledger.domain.aggregates.loan_application import LoanApplicationAggregate
from ledger.domain.errors import DomainError


@dataclass
class WhatIfOutcome:
    state: str | None
    decision: str | None


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


def _evaluate_loan(events: list[dict], application_id: str) -> WhatIfOutcome:
    loan_events = [e for e in events if e.get("stream_id", "").startswith("loan-")]
    loan_events.sort(key=lambda x: x.get("global_position", x.get("stream_position", 0)))
    agg = LoanApplicationAggregate(application_id=application_id, version=-1)
    for ev in loan_events:
        agg._apply(ev)
    return WhatIfOutcome(state=str(agg.state), decision=agg.decision_recommendation)


async def run_what_if(store, application_id: str, branch_global_position: int, counterfactual_events: list[dict]) -> dict:
    """
    Replay history, inject counterfactual events at branch point, and skip causally-dependent events.
    Counterfactual events are never written to the store.
    """
    events = await _collect_events(store, application_id)

    prefix = [e for e in events if e.get("global_position", 0) <= branch_global_position]
    suffix = [e for e in events if e.get("global_position", 0) > branch_global_position]

    # ensure counterfactual events have ids
    cf_events = []
    for ev in counterfactual_events:
        ev = dict(ev)
        ev.setdefault("event_id", f"cf-{uuid4().hex}")
        if not ev.get("stream_id") and (ev.get("payload") or {}).get("application_id") == application_id:
            ev["stream_id"] = f"loan-{application_id}"
        cf_events.append(ev)

    # start skip set with branch event + counterfactual ids
    branch_ids = {e.get("event_id") for e in prefix if e.get("global_position", 0) == branch_global_position}
    skip_causes = set([cid for cid in branch_ids if cid]) | {e.get("event_id") for e in cf_events}

    skipped_ids: list[str] = []
    filtered_suffix: list[dict] = []
    for ev in suffix:
        cause = (ev.get("metadata") or {}).get("causation_id")
        if cause in skip_causes:
            if ev.get("event_id"):
                skipped_ids.append(ev.get("event_id"))
                skip_causes.add(ev.get("event_id"))
            continue
        filtered_suffix.append(ev)

    simulated = prefix + cf_events + filtered_suffix

    real_outcome = _evaluate_loan(events, application_id)
    simulated_outcome = _evaluate_loan(simulated, application_id)

    return {
        "application_id": application_id,
        "branch_global_position": branch_global_position,
        "skipped_event_ids": skipped_ids,
        "real": {
            "state": real_outcome.state,
            "decision": real_outcome.decision,
        },
        "simulated": {
            "state": simulated_outcome.state,
            "decision": simulated_outcome.decision,
        },
    }
