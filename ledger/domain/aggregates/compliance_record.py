"""
ledger/domain/aggregates/compliance_record.py
=============================================
ComplianceRecord aggregate. Replays its event stream to rebuild the
current compliance state for an application.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ledger.domain.errors import DomainError


@dataclass
class ComplianceRecordAggregate:
    application_id: str
    session_id: str | None = None
    regulation_set_version: str | None = None
    rules_to_evaluate: list[str] = field(default_factory=list)
    passed_rules: set[str] = field(default_factory=set)
    failed_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    noted_rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    has_hard_block: bool = False
    overall_verdict: str | None = None
    completed: bool = False
    version: int = -1
    events: list[dict] = field(default_factory=list)

    @classmethod
    async def load(cls, store, application_id: str) -> "ComplianceRecordAggregate":
        if application_id.startswith("compliance-"):
            stream_id = application_id
            app_id = application_id[len("compliance-"):]
        else:
            stream_id = f"compliance-{application_id}"
            app_id = application_id
        agg = cls(application_id=app_id)
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

    def _on_ComplianceCheckInitiated(self, payload: dict) -> None:
        self.session_id = payload.get("session_id")
        self.regulation_set_version = payload.get("regulation_set_version")
        self.rules_to_evaluate = list(payload.get("rules_to_evaluate") or [])
        self.completed = False

    def _on_ComplianceRulePassed(self, payload: dict) -> None:
        rule_id = payload.get("rule_id")
        if rule_id:
            self.passed_rules.add(rule_id)
            self.failed_rules.pop(rule_id, None)

    def _on_ComplianceRuleFailed(self, payload: dict) -> None:
        rule_id = payload.get("rule_id")
        if rule_id:
            self.failed_rules[rule_id] = dict(payload)
            self.passed_rules.discard(rule_id)
        self.has_hard_block = self.has_hard_block or bool(payload.get("is_hard_block"))

    def _on_ComplianceRuleNoted(self, payload: dict) -> None:
        rule_id = payload.get("rule_id") or f"note-{len(self.noted_rules) + 1}"
        self.noted_rules[rule_id] = dict(payload)

    def _on_ComplianceCheckCompleted(self, payload: dict) -> None:
        self.completed = True
        self.has_hard_block = self.has_hard_block or bool(payload.get("has_hard_block"))
        verdict = payload.get("overall_verdict")
        self.overall_verdict = str(verdict) if verdict is not None else self.overall_verdict

    def missing_required_rules(self) -> list[str]:
        return [rule_id for rule_id in self.rules_to_evaluate if rule_id not in self.passed_rules]

    def require_all_mandatory_rules_passed(self) -> None:
        missing = self.missing_required_rules()
        if self.has_hard_block or missing:
            raise DomainError(
                "Compliance requirements are not fully satisfied",
                code="COMPLIANCE_NOT_SATISFIED",
                context={
                    "application_id": self.application_id,
                    "missing_rules": missing,
                    "has_hard_block": self.has_hard_block,
                    "overall_verdict": self.overall_verdict,
                },
            )
