from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException, status

from .auth import AuthContext


ROLE_PROFILES: dict[str, dict] = {
    "loan_officer": {
        "title": "Loan Officer",
        "focus": "Application summary, decision support, and final human review.",
        "resources": ["ledger://applications/{id}", "ledger://ledger/health"],
        "tools": ["submit_application", "record_human_review"],
        "views": ["dashboard"],
    },
    "compliance_officer": {
        "title": "Compliance Officer",
        "focus": "Compliance views, temporal review, and deterministic rule actions.",
        "resources": ["ledger://applications/{id}", "ledger://applications/{id}/compliance", "ledger://ledger/health"],
        "tools": ["record_compliance_check", "run_compliance_agent"],
        "views": ["compliance"],
    },
    "security_officer": {
        "title": "Security Officer",
        "focus": "Integrity checks, logs, outbox health, and audit timeline verification.",
        "resources": ["ledger://applications/{id}/audit-trail", "ledger://ledger/health"],
        "tools": ["run_integrity_check"],
        "views": ["security", "logs", "audit-trail"],
    },
    "admin": {
        "title": "System Administrator",
        "focus": "Cross-system observability, actor management, and platform controls.",
        "resources": ["ledger://applications/{id}", "ledger://applications/{id}/compliance", "ledger://applications/{id}/audit-trail", "ledger://agents/{id}/performance", "ledger://agents/{id}/sessions/{session_id}", "ledger://ledger/health"],
        "tools": ["submit_application", "record_credit_analysis", "record_fraud_screening", "record_compliance_check", "generate_decision", "record_human_review", "start_agent_session", "run_integrity_check", "run_document_processing_agent", "run_credit_analysis_agent", "run_fraud_detection_agent", "run_compliance_agent", "run_decision_orchestrator_agent"],
        "views": ["dashboard", "compliance", "audit-trail", "security", "admin", "logs", "what-if"],
    },
    "auditor": {
        "title": "Auditor",
        "focus": "Read-only audit, compliance, and what-if analysis.",
        "resources": ["ledger://applications/{id}", "ledger://applications/{id}/compliance", "ledger://applications/{id}/audit-trail", "ledger://ledger/health"],
        "tools": [],
        "views": ["audit-trail", "compliance", "what-if"],
    },
    "applicant": {
        "title": "Applicant",
        "focus": "Own applications, submitted documents, and current status.",
        "resources": ["ledger://applications/{id}"],
        "tools": ["submit_application"],
        "views": ["applicant"],
    },
    "user_proxy": {
        "title": "User Proxy",
        "focus": "Scoped automation persona for agent workflows under explicit permissions.",
        "resources": ["ledger://applications/{id}", "ledger://ledger/health"],
        "tools": ["submit_application", "start_agent_session", "run_document_processing_agent", "run_credit_analysis_agent", "run_fraud_detection_agent", "run_compliance_agent", "run_decision_orchestrator_agent"],
        "views": ["dashboard"],
    },
}


TOOL_RATE_LIMITS = {
    "run_integrity_check": 1,
    "record_human_review": 10,
    "submit_application": 20,
}
DEFAULT_TOOL_BUDGET = 30


@dataclass(frozen=True)
class ApplicationAccessRecord:
    application_id: str
    applicant_id: str | None
    tenant_id: str | None
    owner_user_id: str | None


class PolicyEngine:
    def actor_profile(self, role: str) -> dict:
        return ROLE_PROFILES.get(role, ROLE_PROFILES["loan_officer"])

    def session_payload(self, auth: AuthContext) -> dict:
        profile = self.actor_profile(auth.role)
        return {
            "user_id": auth.user_id,
            "role": auth.role,
            "org_id": auth.org_id,
            "is_internal": auth.is_internal,
            "identity_type": auth.identity_type,
            "auth_source": auth.auth_source,
            "display_name": auth.display_name,
            "permissions": sorted(auth.permissions),
            "allowed_tools": profile["tools"],
            "allowed_resources": profile["resources"],
            "allowed_views": profile["views"],
            "capabilities": sorted({*profile["tools"], *profile["resources"], *profile["views"]}),
            "session_mode": "service" if auth.identity_type == "service" else "interactive",
        }

    def actor_profiles(self) -> list[dict]:
        return [
            {
                "role": role,
                "title": profile["title"],
                "focus": profile["focus"],
                "resources": profile["resources"],
                "tools": profile["tools"],
            }
            for role, profile in ROLE_PROFILES.items()
        ]

    def ensure_tool_allowed(self, auth: AuthContext, tool_name: str) -> None:
        if tool_name not in self.actor_profile(auth.role)["tools"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Role {auth.role} cannot invoke tool {tool_name}")

    def ensure_view_allowed(self, auth: AuthContext, view_name: str) -> None:
        if view_name not in self.actor_profile(auth.role)["views"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Role {auth.role} cannot access view {view_name}")

    def ensure_logs_allowed(self, auth: AuthContext) -> None:
        if auth.role not in {"admin", "security_officer"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Logs are restricted to admin/security roles")

    def ensure_same_org(self, auth: AuthContext, record: ApplicationAccessRecord) -> None:
        if record.tenant_id and auth.org_id and record.tenant_id != auth.org_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-tenant access denied")

    def ensure_submit_allowed(self, auth: AuthContext, tenant_id: str | None, owner_user_id: str | None) -> None:
        if auth.role not in {"loan_officer", "admin", "applicant", "user_proxy"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Role {auth.role} cannot create applications")
        if auth.org_id and tenant_id and tenant_id != auth.org_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Applications must be created inside the caller organization")
        if auth.role == "applicant" and owner_user_id and owner_user_id != auth.user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Applicants can only create applications they own")

    def ensure_application_visible(self, auth: AuthContext, record: ApplicationAccessRecord) -> None:
        self.ensure_same_org(auth, record)
        if auth.role == "applicant" and record.owner_user_id and record.owner_user_id != auth.user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Applicants can only access their own applications")

    def ensure_application_mutation_allowed(self, auth: AuthContext, record: ApplicationAccessRecord) -> None:
        self.ensure_application_visible(auth, record)
        if auth.role == "auditor":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Auditors have read-only access")
        if auth.role == "security_officer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Security officers cannot mutate loan applications")
        if auth.role == "applicant":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Applicants cannot run privileged workflow commands")

    def filter_applications(self, auth: AuthContext, rows: Iterable[dict]) -> list[dict]:
        visible: list[dict] = []
        for row in rows:
            record = ApplicationAccessRecord(
                application_id=str(row.get("application_id")),
                applicant_id=row.get("applicant_id"),
                tenant_id=row.get("tenant_id"),
                owner_user_id=row.get("owner_user_id"),
            )
            try:
                self.ensure_application_visible(auth, record)
            except HTTPException:
                continue
            visible.append(row)
        return visible

    def rate_limit_budget(self, tool_name: str) -> int:
        return TOOL_RATE_LIMITS.get(tool_name, DEFAULT_TOOL_BUDGET)


policy_engine = PolicyEngine()
