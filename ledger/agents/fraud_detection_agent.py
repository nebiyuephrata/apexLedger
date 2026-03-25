"""
ledger/agents/fraud_detection_agent.py
=====================================
FraudDetectionAgent implementation.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ledger.agents.base_agent import BaseApexAgent
from ledger.domain.aggregates.loan_application import ApplicationState, LoanApplicationAggregate
from ledger.domain.errors import DomainError
from ledger.schema.events import (
    ComplianceCheckRequested,
    FraudAnomaly,
    FraudAnomalyDetected,
    FraudAnomalyType,
    FraudScreeningCompleted,
    FraudScreeningInitiated,
)


class FraudState(TypedDict):
    application_id: str
    session_id: str
    applicant_id: str | None
    requested_amount_usd: float | None
    extracted_facts: dict | None
    registry_profile: dict | None
    historical_financials: list[dict] | None
    compliance_flags: list[dict] | None
    loan_history: list[dict] | None
    fraud_signals: list[dict] | None
    fraud_score: float | None
    risk_level: str | None
    recommendation: str | None
    anomalies: list[dict] | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


class FraudDetectionAgent(BaseApexAgent):
    """
    Cross-references extracted document facts against historical registry data.
    Detects anomalous discrepancies that suggest fraud or document manipulation.
    """

    REGULATION_SET_VERSION = "2026-Q1"
    RULES_TO_EVALUATE = ["REG-001", "REG-002", "REG-003", "REG-004", "REG-005", "REG-006"]

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
        return [FraudDetectionAgent._to_dict(v) for v in (values or [])]

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_anomaly(raw: dict) -> dict:
        anomaly_type = raw.get("anomaly_type", FraudAnomalyType.UNUSUAL_SUBMISSION_PATTERN.value)
        try:
            anomaly_type = FraudAnomalyType(anomaly_type).value
        except Exception:
            anomaly_type = FraudAnomalyType.UNUSUAL_SUBMISSION_PATTERN.value
        severity = str(raw.get("severity", "MEDIUM")).upper()
        if severity not in {"LOW", "MEDIUM", "HIGH"}:
            severity = "MEDIUM"
        return {
            "anomaly_type": anomaly_type,
            "description": raw.get("description", "Potential anomaly detected."),
            "severity": severity,
            "evidence": raw.get("evidence", "No evidence summary provided."),
            "affected_fields": list(raw.get("affected_fields", [])),
        }

    def _build_fallback_analysis(self, state: FraudState) -> dict:
        facts = state.get("extracted_facts") or {}
        hist = state.get("historical_financials") or []
        anomalies: list[dict] = []

        current_revenue = self._to_float(facts.get("total_revenue"))
        historical_revenue = None
        if hist:
            historical_revenue = self._to_float(hist[-1].get("total_revenue"))
        if current_revenue and historical_revenue and historical_revenue > 0:
            delta = abs(current_revenue - historical_revenue) / historical_revenue
            if delta >= 0.35:
                anomalies.append(
                    self._normalize_anomaly(
                        {
                            "anomaly_type": FraudAnomalyType.REVENUE_DISCREPANCY.value,
                            "description": "Current revenue differs materially from registry history.",
                            "severity": "HIGH" if delta >= 0.60 else "MEDIUM",
                            "evidence": (
                                f"Current revenue={current_revenue:,.0f} vs registry revenue="
                                f"{historical_revenue:,.0f} ({delta:.0%} delta)"
                            ),
                            "affected_fields": ["total_revenue"],
                        }
                    )
                )

        assets = self._to_float(facts.get("total_assets"))
        liabilities = self._to_float(facts.get("total_liabilities"))
        equity = self._to_float(facts.get("total_equity"))
        if None not in (assets, liabilities, equity):
            imbalance = abs(assets - liabilities - equity)
            if imbalance > 1.0:
                anomalies.append(
                    self._normalize_anomaly(
                        {
                            "anomaly_type": FraudAnomalyType.BALANCE_SHEET_INCONSISTENCY.value,
                            "description": "Balance sheet does not reconcile.",
                            "severity": "HIGH" if imbalance > 10_000 else "MEDIUM",
                            "evidence": (
                                f"Assets={assets:,.0f}, liabilities+equity={liabilities + equity:,.0f}, "
                                f"delta={imbalance:,.0f}"
                            ),
                            "affected_fields": ["total_assets", "total_liabilities", "total_equity"],
                        }
                    )
                )

        flags = state.get("compliance_flags") or []
        if any(flag.get("severity") == "HIGH" and flag.get("is_active") for flag in flags):
            anomalies.append(
                self._normalize_anomaly(
                    {
                        "anomaly_type": FraudAnomalyType.UNUSUAL_SUBMISSION_PATTERN.value,
                        "description": "Active high-severity compliance flags increase fraud risk.",
                        "severity": "MEDIUM",
                        "evidence": "Applicant registry contains active HIGH compliance flags.",
                        "affected_fields": [],
                    }
                )
            )

        severity_scores = {"LOW": 0.12, "MEDIUM": 0.24, "HIGH": 0.38}
        fraud_score = min(0.98, sum(severity_scores.get(a["severity"], 0.18) for a in anomalies))
        if fraud_score >= 0.75:
            risk_level = "HIGH"
            recommendation = "ESCALATE"
        elif fraud_score >= 0.40:
            risk_level = "MEDIUM"
            recommendation = "REVIEW"
        else:
            risk_level = "LOW"
            recommendation = "CLEAR"

        return {
            "fraud_score": round(fraud_score, 2),
            "risk_level": risk_level,
            "recommendation": recommendation,
            "anomalies": anomalies,
        }

    def build_graph(self):
        g = StateGraph(FraudState)
        g.add_node("validate_inputs", self._node_validate_inputs)
        g.add_node("load_document_facts", self._node_load_facts)
        g.add_node("cross_reference_registry", self._node_cross_reference)
        g.add_node("analyze_fraud_patterns", self._node_analyze)
        g.add_node("write_output", self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs", "load_document_facts")
        g.add_edge("load_document_facts", "cross_reference_registry")
        g.add_edge("cross_reference_registry", "analyze_fraud_patterns")
        g.add_edge("analyze_fraud_patterns", "write_output")
        g.add_edge("write_output", END)
        return g.compile()

    def _initial_state(self, application_id: str) -> FraudState:
        return FraudState(
            application_id=application_id,
            session_id=self.session_id,
            applicant_id=None,
            requested_amount_usd=None,
            extracted_facts=None,
            registry_profile=None,
            historical_financials=None,
            compliance_flags=None,
            loan_history=None,
            fraud_signals=None,
            fraud_score=None,
            risk_level=None,
            recommendation=None,
            anomalies=None,
            errors=[],
            output_events=[],
            next_agent=None,
        )

    async def _node_validate_inputs(self, state: FraudState) -> FraudState:
        t = time.time()
        app_id = state["application_id"]
        errors: list[str] = []
        app = await LoanApplicationAggregate.load(self.store, app_id)
        fraud_events = await self.store.load_stream(f"fraud-{app_id}")
        credit_events = await self.store.load_stream(f"credit-{app_id}")
        docpkg_events = await self.store.load_stream(f"docpkg-{app_id}")

        try:
            app.require_state(ApplicationState.FRAUD_SCREENING_REQUESTED)
        except DomainError as exc:
            errors.append(str(exc))

        if not app.applicant_id:
            errors.append("LoanApplication is missing applicant_id")
        if app.requested_amount_usd is None:
            errors.append("LoanApplication is missing requested_amount_usd")

        if not any(e.get("event_type") == "CreditAnalysisCompleted" for e in credit_events):
            errors.append("Credit analysis must be completed before fraud screening")
        if not any(e.get("event_type") == "ExtractionCompleted" for e in docpkg_events):
            errors.append("Document package does not contain ExtractionCompleted facts")
        if any(e.get("event_type") == "FraudScreeningCompleted" for e in fraud_events):
            errors.append("Fraud screening already completed")

        ms = int((time.time() - t) * 1000)
        if errors:
            await self._record_input_failed([], errors)
            raise ValueError(f"Input validation failed: {errors}")

        await self._record_input_validated(
            ["application_id", "credit_analysis_completed", "document_facts_available"],
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

    async def _node_load_facts(self, state: FraudState) -> FraudState:
        t = time.time()
        app_id = state["application_id"]
        pkg_events = await self.store.load_stream(f"docpkg-{app_id}")
        extraction_events = [e for e in pkg_events if e.get("event_type") == "ExtractionCompleted"]
        quality_events = [e for e in pkg_events if e.get("event_type") == "QualityAssessmentCompleted"]

        merged_facts: dict[str, Any] = {}
        for event in extraction_events:
            payload = event.get("payload", {}) or {}
            facts = payload.get("facts") or {}
            for key, value in facts.items():
                if value is not None and key not in merged_facts:
                    merged_facts[key] = value

        quality_flags: list[str] = []
        for event in quality_events:
            payload = event.get("payload", {}) or {}
            quality_flags.extend(payload.get("anomalies", []))
            quality_flags.extend(
                [f"CRITICAL_MISSING:{field}" for field in payload.get("critical_missing_fields", [])]
            )

        ms = int((time.time() - t) * 1000)
        await self._record_tool_call(
            "load_event_store_stream",
            f"stream_id=docpkg-{app_id} filter=ExtractionCompleted",
            f"Loaded {len(extraction_events)} extraction events and {len(quality_flags)} quality flags",
            ms,
        )
        await self._record_node_execution(
            "load_document_facts",
            ["docpkg_stream"],
            ["extracted_facts", "fraud_signals"],
            ms,
        )
        return {
            **state,
            "extracted_facts": merged_facts,
            "fraud_signals": quality_flags,
        }

    async def _node_cross_reference(self, state: FraudState) -> FraudState:
        t = time.time()
        applicant_id = state["applicant_id"]
        if not self.registry:
            raise RuntimeError("ApplicantRegistryClient is not configured for FraudDetectionAgent")

        profile = await self.registry.get_company(applicant_id)
        financials = await self.registry.get_financial_history(applicant_id)
        flags = await self.registry.get_compliance_flags(applicant_id)
        loans = await self.registry.get_loan_relationships(applicant_id)

        profile_dict = self._to_dict(profile) if profile else {"company_id": applicant_id}
        financial_dicts = self._to_dict_list(financials)
        flag_dicts = self._to_dict_list(flags)
        loan_dicts = [dict(loan) for loan in loans]

        ms = int((time.time() - t) * 1000)
        await self._record_tool_call(
            "query_applicant_registry",
            f"company_id={applicant_id} tables=[companies,financial_history,compliance_flags,loan_relationships]",
            f"Loaded profile, {len(financial_dicts)} fiscal years, {len(flag_dicts)} flags, {len(loan_dicts)} loans",
            ms,
        )
        await self._record_node_execution(
            "cross_reference_registry",
            ["applicant_id", "extracted_facts"],
            ["registry_profile", "historical_financials", "compliance_flags", "loan_history"],
            ms,
        )
        return {
            **state,
            "registry_profile": profile_dict,
            "historical_financials": financial_dicts,
            "compliance_flags": flag_dicts,
            "loan_history": loan_dicts,
        }

    async def _node_analyze(self, state: FraudState) -> FraudState:
        t = time.time()
        fallback = self._build_fallback_analysis(state)
        ti = to = 0
        cost = 0.0
        result = fallback

        system = """You are a financial fraud detection analyst.
Compare current extracted financial facts against applicant registry history.
Return ONLY JSON:
{
  "fraud_score": <float 0.0-1.0>,
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "recommendation": "CLEAR" | "REVIEW" | "ESCALATE",
  "anomalies": [
    {
      "anomaly_type": "REVENUE_DISCREPANCY" | "BALANCE_SHEET_INCONSISTENCY" | "UNUSUAL_SUBMISSION_PATTERN" | "IDENTITY_MISMATCH" | "DOCUMENT_ALTERATION_SUSPECTED",
      "description": "<plain english>",
      "severity": "LOW" | "MEDIUM" | "HIGH",
      "evidence": "<short evidence summary>",
      "affected_fields": ["field_name"]
    }
  ]
}
Do not make compliance or lending decisions."""

        user = json.dumps(
            {
                "application_id": state["application_id"],
                "requested_amount_usd": state.get("requested_amount_usd"),
                "extracted_facts": state.get("extracted_facts"),
                "quality_flags": state.get("fraud_signals") or [],
                "registry_profile": state.get("registry_profile") or {},
                "historical_financials": state.get("historical_financials") or [],
                "compliance_flags": state.get("compliance_flags") or [],
                "loan_history": state.get("loan_history") or [],
            },
            default=str,
        )

        try:
            content, ti, to, cost = await self._call_llm(system, user, max_tokens=1024)
            parsed = self._parse_json(content)
            anomalies = [self._normalize_anomaly(item) for item in parsed.get("anomalies", [])]
            result = {
                "fraud_score": max(0.0, min(1.0, float(parsed.get("fraud_score", fallback["fraud_score"])))),
                "risk_level": str(parsed.get("risk_level", fallback["risk_level"])).upper(),
                "recommendation": str(parsed.get("recommendation", fallback["recommendation"])).upper(),
                "anomalies": anomalies or fallback["anomalies"],
            }
        except Exception:
            result = fallback

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "analyze_fraud_patterns",
            ["extracted_facts", "historical_financials", "registry_profile", "compliance_flags", "loan_history"],
            ["fraud_score", "risk_level", "anomalies"],
            ms,
            ti,
            to,
            cost,
        )
        return {
            **state,
            "fraud_score": result["fraud_score"],
            "risk_level": result["risk_level"],
            "recommendation": result["recommendation"],
            "anomalies": result["anomalies"],
        }

    async def _node_write_output(self, state: FraudState) -> FraudState:
        t = time.time()
        app_id = state["application_id"]
        fraud_stream = f"fraud-{app_id}"

        events: list[dict] = [
            FraudScreeningInitiated(
                application_id=app_id,
                session_id=self.session_id,
                screening_model_version=self.model,
                initiated_at=datetime.now(),
            ).to_store_dict()
        ]

        anomalies = state.get("anomalies") or []
        for anomaly in anomalies:
            events.append(
                FraudAnomalyDetected(
                    application_id=app_id,
                    session_id=self.session_id,
                    anomaly=FraudAnomaly(**anomaly),
                    detected_at=datetime.now(),
                ).to_store_dict()
            )

        events.append(
            FraudScreeningCompleted(
                application_id=app_id,
                session_id=self.session_id,
                fraud_score=float(state.get("fraud_score") or 0.0),
                risk_level=state.get("risk_level") or "LOW",
                anomalies_found=len(anomalies),
                recommendation=state.get("recommendation") or "CLEAR",
                screening_model_version=self.model,
                input_data_hash=self._sha(state),
                completed_at=datetime.now(),
            ).to_store_dict()
        )
        fraud_positions = await self._append_with_retry(fraud_stream, events, causation_id=self.session_id)

        trigger = ComplianceCheckRequested(
            application_id=app_id,
            requested_at=datetime.now(),
            triggered_by_event_id=self.session_id,
            regulation_set_version=self.REGULATION_SET_VERSION,
            rules_to_evaluate=self.RULES_TO_EVALUATE,
        ).to_store_dict()
        loan_positions = await self._append_with_retry(
            f"loan-{app_id}",
            [trigger],
            causation_id=self.session_id,
        )

        events_written = [
            {
                "stream_id": fraud_stream,
                "event_type": event["event_type"],
                "stream_position": fraud_positions[index] if index < len(fraud_positions) else -1,
            }
            for index, event in enumerate(events)
        ]
        events_written.append(
            {
                "stream_id": f"loan-{app_id}",
                "event_type": "ComplianceCheckRequested",
                "stream_position": loan_positions[0] if loan_positions else -1,
            }
        )

        await self._record_output_written(
            events_written,
            f"Fraud score {state.get('fraud_score', 0.0):.0%}; compliance screening triggered.",
        )
        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "write_output",
            ["fraud_score", "risk_level", "anomalies"],
            ["events_written"],
            ms,
        )
        return {
            **state,
            "output_events": events_written,
            "next_agent": "compliance",
            "next_agent_triggered": "compliance",
        }
