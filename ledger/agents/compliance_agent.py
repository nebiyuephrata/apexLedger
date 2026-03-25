"""
ledger/agents/compliance_agent.py
=================================
ComplianceAgent implementation.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ledger.agents.base_agent import BaseApexAgent
from ledger.domain.aggregates.loan_application import ApplicationState, LoanApplicationAggregate
from ledger.domain.errors import DomainError
from ledger.schema.events import (
    ApplicationDeclined,
    ComplianceCheckCompleted,
    ComplianceCheckInitiated,
    ComplianceRuleFailed,
    ComplianceRuleNoted,
    ComplianceRulePassed,
    ComplianceVerdict,
    DecisionRequested,
)


class ComplianceState(TypedDict):
    application_id: str
    session_id: str
    applicant_id: str | None
    requested_amount_usd: float | None
    company_profile: dict | None
    rule_results: list[dict] | None
    has_hard_block: bool
    block_rule_id: str | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


REGULATIONS = {
    "REG-001": {
        "name": "Bank Secrecy Act (BSA) Check",
        "version": "2026-Q1-v1",
        "is_hard_block": False,
        "check": lambda co: not any(
            f.get("flag_type") == "AML_WATCH" and f.get("is_active")
            for f in co.get("compliance_flags", [])
        ),
        "failure_reason": "Active AML Watch flag present. Remediation required.",
        "remediation": "Provide enhanced due diligence documentation within 10 business days.",
    },
    "REG-002": {
        "name": "OFAC Sanctions Screening",
        "version": "2026-Q1-v1",
        "is_hard_block": True,
        "check": lambda co: not any(
            f.get("flag_type") == "SANCTIONS_REVIEW" and f.get("is_active")
            for f in co.get("compliance_flags", [])
        ),
        "failure_reason": "Active OFAC Sanctions Review. Application blocked.",
        "remediation": None,
    },
    "REG-003": {
        "name": "Jurisdiction Lending Eligibility",
        "version": "2026-Q1-v1",
        "is_hard_block": True,
        "check": lambda co: co.get("jurisdiction") != "MT",
        "failure_reason": "Jurisdiction MT not approved for commercial lending at this time.",
        "remediation": None,
    },
    "REG-004": {
        "name": "Legal Entity Type Eligibility",
        "version": "2026-Q1-v1",
        "is_hard_block": False,
        "check": lambda co: not (
            co.get("legal_type") == "Sole Proprietor"
            and (co.get("requested_amount_usd", 0) or 0) > 250_000
        ),
        "failure_reason": "Sole Proprietor loans >$250K require additional documentation.",
        "remediation": "Submit SBA Form 912 and personal financial statement.",
    },
    "REG-005": {
        "name": "Minimum Operating History",
        "version": "2026-Q1-v1",
        "is_hard_block": True,
        "check": lambda co: (datetime.now().year - (co.get("founded_year") or datetime.now().year)) >= 2,
        "failure_reason": "Business must have at least 2 years of operating history.",
        "remediation": None,
    },
    "REG-006": {
        "name": "CRA Community Reinvestment",
        "version": "2026-Q1-v1",
        "is_hard_block": False,
        "check": lambda co: True,
        "note_type": "CRA_CONSIDERATION",
        "note_text": "Jurisdiction qualifies for Community Reinvestment Act consideration.",
    },
}


class ComplianceAgent(BaseApexAgent):
    """Evaluates deterministic regulatory rules and hard-blocks Montana immediately."""

    REGULATION_SET_VERSION = "2026-Q1"
    RULE_SEQUENCE = ["REG-001", "REG-002", "REG-003", "REG-004", "REG-005", "REG-006"]

    @staticmethod
    def _to_dict(value: Any) -> dict:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "__dict__"):
            return dict(vars(value))
        raise TypeError(f"Unsupported registry payload type: {type(value)!r}")

    @staticmethod
    def _to_dict_list(values: list[Any] | None) -> list[dict]:
        return [ComplianceAgent._to_dict(v) for v in (values or [])]

    def _rule_node(self, rule_id: str):
        async def _runner(state: ComplianceState) -> ComplianceState:
            return await self._evaluate_rule(state, rule_id)

        return _runner

    def build_graph(self):
        g = StateGraph(ComplianceState)
        g.add_node("validate_inputs", self._node_validate_inputs)
        g.add_node("load_company_profile", self._node_load_profile)
        g.add_node("evaluate_reg001", self._rule_node("REG-001"))
        g.add_node("evaluate_reg002", self._rule_node("REG-002"))
        g.add_node("evaluate_reg003", self._rule_node("REG-003"))
        g.add_node("evaluate_reg004", self._rule_node("REG-004"))
        g.add_node("evaluate_reg005", self._rule_node("REG-005"))
        g.add_node("evaluate_reg006", self._rule_node("REG-006"))
        g.add_node("write_output", self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs", "load_company_profile")
        g.add_edge("load_company_profile", "evaluate_reg001")

        for src, nxt in [
            ("evaluate_reg001", "evaluate_reg002"),
            ("evaluate_reg002", "evaluate_reg003"),
            ("evaluate_reg003", "evaluate_reg004"),
            ("evaluate_reg004", "evaluate_reg005"),
            ("evaluate_reg005", "evaluate_reg006"),
            ("evaluate_reg006", "write_output"),
        ]:
            g.add_conditional_edges(
                src,
                lambda s, _nxt=nxt: "write_output" if s["has_hard_block"] else _nxt,
            )
        g.add_edge("write_output", END)
        return g.compile()

    def _initial_state(self, application_id: str) -> ComplianceState:
        return ComplianceState(
            application_id=application_id,
            session_id=self.session_id,
            applicant_id=None,
            requested_amount_usd=None,
            company_profile=None,
            rule_results=[],
            has_hard_block=False,
            block_rule_id=None,
            errors=[],
            output_events=[],
            next_agent=None,
        )

    async def _node_validate_inputs(self, state: ComplianceState) -> ComplianceState:
        t = time.time()
        app_id = state["application_id"]
        errors: list[str] = []
        app = await LoanApplicationAggregate.load(self.store, app_id)
        compliance_events = await self.store.load_stream(f"compliance-{app_id}")

        try:
            app.require_state(ApplicationState.COMPLIANCE_CHECK_REQUESTED)
        except DomainError as exc:
            errors.append(str(exc))

        if not app.applicant_id:
            errors.append("LoanApplication is missing applicant_id")
        if app.requested_amount_usd is None:
            errors.append("LoanApplication is missing requested_amount_usd")
        if any(e.get("event_type") == "ComplianceCheckCompleted" for e in compliance_events):
            errors.append("Compliance check already completed")

        ms = int((time.time() - t) * 1000)
        if errors:
            await self._record_input_failed([], errors)
            raise ValueError(f"Input validation failed: {errors}")

        await self._record_input_validated(
            ["application_id", "compliance_requested"],
            ms,
        )
        await self._record_node_execution(
            "validate_inputs",
            ["application_id"],
            ["applicant_id", "requested_amount_usd"],
            ms,
        )
        return {
            **state,
            "applicant_id": app.applicant_id,
            "requested_amount_usd": float(app.requested_amount_usd),
            "errors": errors,
        }

    async def _node_load_profile(self, state: ComplianceState) -> ComplianceState:
        t = time.time()
        applicant_id = state["applicant_id"]
        if not self.registry:
            raise RuntimeError("ApplicantRegistryClient is not configured for ComplianceAgent")

        profile = await self.registry.get_company(applicant_id)
        flags = await self.registry.get_compliance_flags(applicant_id)

        profile_dict = self._to_dict(profile) if profile else {"company_id": applicant_id}
        profile_dict["requested_amount_usd"] = state.get("requested_amount_usd")
        profile_dict["compliance_flags"] = self._to_dict_list(flags)

        ms = int((time.time() - t) * 1000)
        await self._record_tool_call(
            "query_applicant_registry",
            f"company_id={applicant_id} tables=[companies,compliance_flags]",
            f"Loaded company profile and {len(profile_dict['compliance_flags'])} compliance flags",
            ms,
        )
        await self._record_node_execution(
            "load_company_profile",
            ["applicant_id", "requested_amount_usd"],
            ["company_profile"],
            ms,
        )
        return {**state, "company_profile": profile_dict}

    async def _evaluate_rule(self, state: ComplianceState, rule_id: str) -> ComplianceState:
        t = time.time()
        rule = REGULATIONS[rule_id]
        profile = dict(state.get("company_profile") or {})
        rule_results = list(state.get("rule_results") or [])

        outcome: dict
        passed = bool(rule["check"](profile))
        if not passed:
            outcome = {
                "status": "failed",
                "rule_id": rule_id,
                "rule_name": rule["name"],
                "rule_version": rule["version"],
                "failure_reason": rule["failure_reason"],
                "is_hard_block": bool(rule["is_hard_block"]),
                "remediation_available": rule.get("remediation") is not None,
                "remediation_description": rule.get("remediation"),
                "evidence_hash": self._sha(profile),
            }
        elif "note_type" in rule:
            outcome = {
                "status": "noted",
                "rule_id": rule_id,
                "rule_name": rule["name"],
                "note_type": rule["note_type"],
                "note_text": rule["note_text"],
            }
        else:
            outcome = {
                "status": "passed",
                "rule_id": rule_id,
                "rule_name": rule["name"],
                "rule_version": rule["version"],
                "evaluation_notes": "Rule passed based on current applicant profile and flags.",
                "evidence_hash": self._sha(profile),
            }

        rule_results.append(outcome)
        has_hard_block = state["has_hard_block"] or (
            outcome["status"] == "failed" and bool(outcome.get("is_hard_block"))
        )
        block_rule_id = state["block_rule_id"] or (
            outcome["rule_id"] if outcome["status"] == "failed" and outcome.get("is_hard_block") else None
        )

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            f"evaluate_{rule_id.lower()}",
            ["company_profile", "rule_results"],
            ["rule_results", "has_hard_block"],
            ms,
        )
        return {
            **state,
            "rule_results": rule_results,
            "has_hard_block": has_hard_block,
            "block_rule_id": block_rule_id,
        }

    async def _node_write_output(self, state: ComplianceState) -> ComplianceState:
        t = time.time()
        app_id = state["application_id"]
        comp_stream = f"compliance-{app_id}"

        rule_results = state.get("rule_results") or []
        comp_events: list[dict] = [
            ComplianceCheckInitiated(
                application_id=app_id,
                session_id=self.session_id,
                regulation_set_version=self.REGULATION_SET_VERSION,
                rules_to_evaluate=[result["rule_id"] for result in rule_results],
                initiated_at=datetime.now(),
            ).to_store_dict()
        ]

        passed = failed = noted = 0
        for result in rule_results:
            if result["status"] == "passed":
                passed += 1
                comp_events.append(
                    ComplianceRulePassed(
                        application_id=app_id,
                        session_id=self.session_id,
                        rule_id=result["rule_id"],
                        rule_name=result["rule_name"],
                        rule_version=result["rule_version"],
                        evidence_hash=result["evidence_hash"],
                        evaluation_notes=result["evaluation_notes"],
                        evaluated_at=datetime.now(),
                    ).to_store_dict()
                )
            elif result["status"] == "failed":
                failed += 1
                comp_events.append(
                    ComplianceRuleFailed(
                        application_id=app_id,
                        session_id=self.session_id,
                        rule_id=result["rule_id"],
                        rule_name=result["rule_name"],
                        rule_version=result["rule_version"],
                        failure_reason=result["failure_reason"],
                        is_hard_block=bool(result["is_hard_block"]),
                        remediation_available=bool(result["remediation_available"]),
                        remediation_description=result.get("remediation_description"),
                        evidence_hash=result["evidence_hash"],
                        evaluated_at=datetime.now(),
                    ).to_store_dict()
                )
            else:
                noted += 1
                comp_events.append(
                    ComplianceRuleNoted(
                        application_id=app_id,
                        session_id=self.session_id,
                        rule_id=result["rule_id"],
                        rule_name=result["rule_name"],
                        note_type=result["note_type"],
                        note_text=result["note_text"],
                        evaluated_at=datetime.now(),
                    ).to_store_dict()
                )

        verdict = ComplianceVerdict.BLOCKED if state["has_hard_block"] else ComplianceVerdict.CLEAR
        comp_events.append(
            ComplianceCheckCompleted(
                application_id=app_id,
                session_id=self.session_id,
                rules_evaluated=len(rule_results),
                rules_passed=passed,
                rules_failed=failed,
                rules_noted=noted,
                has_hard_block=state["has_hard_block"],
                overall_verdict=verdict,
                completed_at=datetime.now(),
            ).to_store_dict()
        )
        comp_positions = await self._append_with_retry(comp_stream, comp_events, causation_id=self.session_id)

        loan_events: list[dict] = []
        if state["has_hard_block"]:
            block_rule = state.get("block_rule_id") or "COMPLIANCE_BLOCK"
            loan_events.append(
                ApplicationDeclined(
                    application_id=app_id,
                    decline_reasons=[block_rule],
                    declined_by="compliance_agent",
                    adverse_action_notice_required=True,
                    adverse_action_codes=[block_rule],
                    declined_at=datetime.now(),
                ).to_store_dict()
            )
        else:
            loan_events.append(
                DecisionRequested(
                    application_id=app_id,
                    requested_at=datetime.now(),
                    all_analyses_complete=True,
                    triggered_by_event_id=self.session_id,
                ).to_store_dict()
            )
        loan_positions = await self._append_with_retry(
            f"loan-{app_id}",
            loan_events,
            causation_id=self.session_id,
        )

        events_written = [
            {
                "stream_id": comp_stream,
                "event_type": event["event_type"],
                "stream_position": comp_positions[index] if index < len(comp_positions) else -1,
            }
            for index, event in enumerate(comp_events)
        ]
        for index, event in enumerate(loan_events):
            events_written.append(
                {
                    "stream_id": f"loan-{app_id}",
                    "event_type": event["event_type"],
                    "stream_position": loan_positions[index] if index < len(loan_positions) else -1,
                }
            )

        summary = (
            f"Compliance verdict {verdict.value}; "
            f"{'application declined' if state['has_hard_block'] else 'decision requested'}."
        )
        await self._record_output_written(events_written, summary)
        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "write_output",
            ["rule_results", "has_hard_block"],
            ["events_written"],
            ms,
        )
        return {
            **state,
            "output_events": events_written,
            "next_agent": None if state["has_hard_block"] else "decision_orchestrator",
            "next_agent_triggered": None if state["has_hard_block"] else "decision_orchestrator",
        }
