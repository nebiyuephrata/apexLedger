"""
ledger/domain/aggregates/loan_application.py
============================================
LoanApplication aggregate. Replays its stream to rebuild state.

IMPORTANT:
  - _on_* methods only update internal state (no validation or I/O).
  - Business rule validation lives in aggregate guard methods so handlers can
    follow the CQRS load → validate → determine → append pattern.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ledger.domain.errors import DomainError

class ApplicationState(str, Enum):
    NEW = "NEW"; SUBMITTED = "SUBMITTED"; DOCUMENTS_PENDING = "DOCUMENTS_PENDING"
    DOCUMENTS_UPLOADED = "DOCUMENTS_UPLOADED"; DOCUMENTS_PROCESSED = "DOCUMENTS_PROCESSED"
    CREDIT_ANALYSIS_REQUESTED = "CREDIT_ANALYSIS_REQUESTED"; CREDIT_ANALYSIS_COMPLETE = "CREDIT_ANALYSIS_COMPLETE"
    FRAUD_SCREENING_REQUESTED = "FRAUD_SCREENING_REQUESTED"; FRAUD_SCREENING_COMPLETE = "FRAUD_SCREENING_COMPLETE"
    COMPLIANCE_CHECK_REQUESTED = "COMPLIANCE_CHECK_REQUESTED"; COMPLIANCE_CHECK_COMPLETE = "COMPLIANCE_CHECK_COMPLETE"
    PENDING_DECISION = "PENDING_DECISION"; PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    APPROVED_PENDING_HUMAN = "APPROVED_PENDING_HUMAN"; DECLINED_PENDING_HUMAN = "DECLINED_PENDING_HUMAN"
    APPROVED = "APPROVED"; DECLINED = "DECLINED"; DECLINED_COMPLIANCE = "DECLINED_COMPLIANCE"
    REFERRED = "REFERRED"
    WITHDRAWN = "WITHDRAWN"

VALID_TRANSITIONS = {
    ApplicationState.NEW: [ApplicationState.SUBMITTED],
    ApplicationState.SUBMITTED: [ApplicationState.DOCUMENTS_PENDING],
    ApplicationState.DOCUMENTS_PENDING: [ApplicationState.DOCUMENTS_UPLOADED],
    ApplicationState.DOCUMENTS_UPLOADED: [ApplicationState.DOCUMENTS_PROCESSED, ApplicationState.CREDIT_ANALYSIS_REQUESTED],
    ApplicationState.DOCUMENTS_PROCESSED: [ApplicationState.CREDIT_ANALYSIS_REQUESTED],
    ApplicationState.CREDIT_ANALYSIS_REQUESTED: [ApplicationState.FRAUD_SCREENING_REQUESTED],
    ApplicationState.CREDIT_ANALYSIS_COMPLETE: [ApplicationState.FRAUD_SCREENING_REQUESTED],
    ApplicationState.FRAUD_SCREENING_REQUESTED: [ApplicationState.COMPLIANCE_CHECK_REQUESTED],
    ApplicationState.FRAUD_SCREENING_COMPLETE: [ApplicationState.COMPLIANCE_CHECK_REQUESTED],
    ApplicationState.COMPLIANCE_CHECK_REQUESTED: [ApplicationState.PENDING_DECISION, ApplicationState.DECLINED_COMPLIANCE],
    ApplicationState.COMPLIANCE_CHECK_COMPLETE: [ApplicationState.PENDING_DECISION, ApplicationState.DECLINED_COMPLIANCE],
    ApplicationState.PENDING_DECISION: [
        ApplicationState.APPROVED_PENDING_HUMAN,
        ApplicationState.DECLINED_PENDING_HUMAN,
        ApplicationState.PENDING_HUMAN_REVIEW,
        ApplicationState.REFERRED,
        ApplicationState.APPROVED,
        ApplicationState.DECLINED,
    ],
    ApplicationState.APPROVED_PENDING_HUMAN: [ApplicationState.APPROVED],
    ApplicationState.DECLINED_PENDING_HUMAN: [ApplicationState.DECLINED],
    ApplicationState.PENDING_HUMAN_REVIEW: [ApplicationState.APPROVED, ApplicationState.DECLINED],
}

@dataclass
class LoanApplicationAggregate:
    application_id: str
    state: ApplicationState = ApplicationState.NEW
    applicant_id: str | None = None
    requested_amount_usd: float | None = None
    loan_purpose: str | None = None
    loan_term_months: int | None = None
    submission_channel: str | None = None
    contact_email: str | None = None
    contact_name: str | None = None
    application_reference: str | None = None
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision_recommendation: str | None = None
    decision_confidence: float | None = None
    approved_amount_usd: float | None = None
    version: int = -1
    events: list[dict] = field(default_factory=list)

    @property
    def canonical_state(self) -> str:
        mapping = {
            ApplicationState.SUBMITTED: "Submitted",
            ApplicationState.CREDIT_ANALYSIS_REQUESTED: "AwaitingAnalysis",
            ApplicationState.FRAUD_SCREENING_REQUESTED: "AwaitingAnalysis",
            ApplicationState.CREDIT_ANALYSIS_COMPLETE: "AnalysisComplete",
            ApplicationState.FRAUD_SCREENING_COMPLETE: "AnalysisComplete",
            ApplicationState.COMPLIANCE_CHECK_REQUESTED: "ComplianceReview",
            ApplicationState.COMPLIANCE_CHECK_COMPLETE: "ComplianceReview",
            ApplicationState.PENDING_DECISION: "PendingDecision",
            ApplicationState.APPROVED_PENDING_HUMAN: "ApprovedPendingHuman",
            ApplicationState.DECLINED_PENDING_HUMAN: "DeclinedPendingHuman",
            ApplicationState.PENDING_HUMAN_REVIEW: "DeclinedPendingHuman",
            ApplicationState.APPROVED: "FinalApproved",
            ApplicationState.DECLINED: "FinalDeclined",
            ApplicationState.DECLINED_COMPLIANCE: "FinalDeclined",
        }
        return mapping.get(self.state, self.state.value.title())

    @classmethod
    async def load(cls, store, application_id: str) -> "LoanApplicationAggregate":
        """Load and replay the loan-{application_id} stream."""
        if application_id.startswith("loan-"):
            stream_id = application_id
            app_id = application_id[len("loan-"):]
        else:
            stream_id = f"loan-{application_id}"
            app_id = application_id
        agg = cls(application_id=app_id, version=-1)
        events = await store.load_stream(stream_id)
        for event in events:
            agg._apply(event)
        return agg

    def _apply(self, event: dict) -> None:
        """Dispatch to _on_* handlers for state reconstruction."""
        et = event.get("event_type")
        p = event.get("payload", {})
        target = self._target_state_for_event(et, p)
        if target is not None:
            # Multiple DocumentUploaded events are valid while the loan remains
            # in DOCUMENTS_UPLOADED; each upload adds more package material.
            if not (et == "DocumentUploaded" and target == self.state == ApplicationState.DOCUMENTS_UPLOADED):
                self.assert_valid_transition(target)
        handler = getattr(self, f"_on_{et}", None)
        if handler:
            handler(p)
        # Use stream_position when present (event store starts at 1)
        if "stream_position" in event:
            self.version = event["stream_position"]
        else:
            self.version += 1
        self.events.append(event)

    def _target_state_for_event(self, et: str | None, p: dict) -> ApplicationState | None:
        if not et:
            return None
        if et == "ApplicationSubmitted": return ApplicationState.SUBMITTED
        if et == "DocumentUploadRequested": return ApplicationState.DOCUMENTS_PENDING
        if et == "DocumentUploaded": return ApplicationState.DOCUMENTS_UPLOADED
        if et == "CreditAnalysisRequested": return ApplicationState.CREDIT_ANALYSIS_REQUESTED
        if et == "FraudScreeningRequested": return ApplicationState.FRAUD_SCREENING_REQUESTED
        if et == "ComplianceCheckRequested": return ApplicationState.COMPLIANCE_CHECK_REQUESTED
        if et == "DecisionRequested": return ApplicationState.PENDING_DECISION
        if et == "DecisionGenerated":
            rec = (p.get("recommendation") or "").upper()
            if rec in ("REFER", "REFERRED", "HUMAN_REVIEW"):
                return ApplicationState.PENDING_HUMAN_REVIEW
            if rec == "APPROVE":
                return ApplicationState.APPROVED_PENDING_HUMAN
            if rec == "DECLINE":
                return ApplicationState.DECLINED_PENDING_HUMAN
            return None
        if et == "HumanReviewRequested": return ApplicationState.PENDING_HUMAN_REVIEW
        if et == "ApplicationApproved": return ApplicationState.APPROVED
        if et == "ApplicationDeclined":
            reasons = [str(r).upper() for r in (p.get("decline_reasons") or [])]
            if any("REG-003" in r or "COMPLIANCE" in r for r in reasons):
                return ApplicationState.DECLINED_COMPLIANCE
            return ApplicationState.DECLINED
        if et == "ApplicationWithdrawn": return ApplicationState.WITHDRAWN
        return None

    # ─── EVENT HANDLERS (NO VALIDATION) ──────────────────────────────────────

    def _on_ApplicationSubmitted(self, p: dict) -> None:
        self.state = ApplicationState.SUBMITTED
        self.applicant_id = p.get("applicant_id")
        self.requested_amount_usd = float(p.get("requested_amount_usd")) if p.get("requested_amount_usd") is not None else None
        self.loan_purpose = p.get("loan_purpose")
        self.loan_term_months = p.get("loan_term_months")
        self.submission_channel = p.get("submission_channel")
        self.contact_email = p.get("contact_email")
        self.contact_name = p.get("contact_name")
        self.application_reference = p.get("application_reference")

    def _on_DocumentUploadRequested(self, p: dict) -> None:
        self.state = ApplicationState.DOCUMENTS_PENDING

    def _on_DocumentUploaded(self, p: dict) -> None:
        self.state = ApplicationState.DOCUMENTS_UPLOADED
        doc_id = p.get("document_id")
        if doc_id:
            self.documents[doc_id] = {
                "document_type": p.get("document_type"),
                "document_format": p.get("document_format"),
                "file_path": p.get("file_path"),
                "file_hash": p.get("file_hash"),
                "uploaded_at": p.get("uploaded_at"),
            }

    def _on_DocumentUploadFailed(self, p: dict) -> None:
        # State remains DOCUMENTS_PENDING; record last error if needed
        pass

    def _on_CreditAnalysisRequested(self, p: dict) -> None:
        self.state = ApplicationState.CREDIT_ANALYSIS_REQUESTED

    def _on_FraudScreeningRequested(self, p: dict) -> None:
        self.state = ApplicationState.FRAUD_SCREENING_REQUESTED

    def _on_ComplianceCheckRequested(self, p: dict) -> None:
        self.state = ApplicationState.COMPLIANCE_CHECK_REQUESTED

    def _on_DecisionRequested(self, p: dict) -> None:
        self.state = ApplicationState.PENDING_DECISION

    def _on_DecisionGenerated(self, p: dict) -> None:
        conf = p.get("confidence")
        rec = p.get("recommendation")
        # Confidence floor enforcement (domain invariant)
        if conf is not None and float(conf) < 0.60:
            rec = "REFER"
        self.decision_recommendation = rec
        self.decision_confidence = conf
        approved_amt = p.get("approved_amount_usd")
        self.approved_amount_usd = float(approved_amt) if approved_amt is not None else None
        rec = (rec or "").upper()
        if rec in ("REFER", "REFERRED", "HUMAN_REVIEW"):
            self.state = ApplicationState.PENDING_HUMAN_REVIEW
        elif rec == "APPROVE":
            self.state = ApplicationState.APPROVED_PENDING_HUMAN
        elif rec == "DECLINE":
            self.state = ApplicationState.DECLINED_PENDING_HUMAN

    def _on_HumanReviewRequested(self, p: dict) -> None:
        self.state = ApplicationState.PENDING_HUMAN_REVIEW

    def _on_HumanReviewCompleted(self, p: dict) -> None:
        # Final decision applied by ApplicationApproved/ApplicationDeclined
        pass

    def _on_ApplicationApproved(self, p: dict) -> None:
        self.state = ApplicationState.APPROVED
        approved_amt = p.get("approved_amount_usd")
        self.approved_amount_usd = float(approved_amt) if approved_amt is not None else None

    def _on_ApplicationDeclined(self, p: dict) -> None:
        reasons = [str(r).upper() for r in (p.get("decline_reasons") or [])]
        if any("REG-003" in r or "COMPLIANCE" in r for r in reasons):
            self.state = ApplicationState.DECLINED_COMPLIANCE
        else:
            self.state = ApplicationState.DECLINED

    def _on_ApplicationWithdrawn(self, p: dict) -> None:
        self.state = ApplicationState.WITHDRAWN

    def assert_valid_transition(self, target: ApplicationState) -> None:
        allowed = VALID_TRANSITIONS.get(self.state, [])
        if target not in allowed:
            raise DomainError(
                f"Invalid transition {self.state} → {target}. Allowed: {allowed}",
                code="INVALID_STATE_TRANSITION",
                context={"current_state": self.state.value, "target_state": target.value},
            )

    def require_state(self, *states: ApplicationState) -> None:
        if self.state not in states:
            allowed = ", ".join([s.value for s in states])
            raise DomainError(
                f"Invalid state {self.state}; expected one of: {allowed}",
                code="INVALID_APPLICATION_STATE",
                context={"current_state": self.state.value, "allowed_states": [s.value for s in states]},
            )

    def allow_withdrawal_from(self) -> None:
        """Validate that withdrawal can only occur before final decision."""
        if self.state in (ApplicationState.APPROVED, ApplicationState.DECLINED, ApplicationState.DECLINED_COMPLIANCE):
            raise DomainError("Cannot withdraw after final decision", code="WITHDRAWAL_NOT_ALLOWED")

    def require_can_submit(self) -> None:
        if self.version != -1 or self.state != ApplicationState.NEW:
            raise DomainError(
                f"Application {self.application_id} already exists",
                code="APPLICATION_ALREADY_EXISTS",
                context={"application_id": self.application_id, "current_state": self.state.value, "version": self.version},
            )

    def require_credit_analysis_ready(self) -> None:
        self.require_state(
            ApplicationState.DOCUMENTS_UPLOADED,
            ApplicationState.DOCUMENTS_PROCESSED,
            ApplicationState.CREDIT_ANALYSIS_REQUESTED,
        )

    def has_human_override(self) -> bool:
        return any(
            ev.get("event_type") == "HumanReviewCompleted"
            and bool(ev.get("payload", {}).get("override"))
            for ev in self.events
        )

    def require_credit_analysis_unlocked(self, has_existing_analysis: bool) -> None:
        """Rule: model version locking (no re-analysis without human override)."""
        if has_existing_analysis and not self.has_human_override():
            raise DomainError(
                "CreditAnalysisCompleted already exists without HumanReview override",
                code="MODEL_VERSION_LOCKED",
                context={"application_id": self.application_id},
            )

    def require_decision_generation_ready(self) -> None:
        self.require_state(
            ApplicationState.PENDING_DECISION,
            ApplicationState.COMPLIANCE_CHECK_REQUESTED,
            ApplicationState.COMPLIANCE_CHECK_COMPLETE,
        )

    def enforce_confidence_floor(self, recommendation: str | None, confidence: float | None) -> str | None:
        """Rule: confidence floor (below 0.60 forces REFER)."""
        if confidence is not None and float(confidence) < 0.60:
            return "REFER"
        return recommendation

    def require_approval_state(self) -> None:
        self.require_state(
            ApplicationState.PENDING_DECISION,
            ApplicationState.APPROVED_PENDING_HUMAN,
            ApplicationState.PENDING_HUMAN_REVIEW,
            ApplicationState.REFERRED,
        )

    def require_decline_state(self) -> None:
        self.require_state(
            ApplicationState.PENDING_DECISION,
            ApplicationState.DECLINED_PENDING_HUMAN,
            ApplicationState.PENDING_HUMAN_REVIEW,
            ApplicationState.REFERRED,
        )

    def require_contributing_sessions(
        self,
        contributing_session_ids: list[str],
        sessions: list[Any],
    ) -> None:
        """Rule: causal chain enforcement for contributing agent sessions."""
        session_map = {getattr(session, "session_id", None): session for session in sessions if getattr(session, "session_id", None)}
        for sid in contributing_session_ids:
            session = session_map.get(sid)
            if session is None:
                raise DomainError(
                    f"Contributing session {sid} is not tied to application {self.application_id}",
                    code="INVALID_CAUSAL_CHAIN",
                    context={"application_id": self.application_id, "session_id": sid},
                )
            session.require_started()
            session.require_application(self.application_id)
