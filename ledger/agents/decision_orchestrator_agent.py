"""
ledger/agents/decision_orchestrator_agent.py
============================================
DecisionOrchestratorAgent implementation.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ledger.agents.base_agent import BaseApexAgent
from ledger.commands.handlers import handle_decision_generated
from ledger.domain.aggregates.loan_application import ApplicationState, LoanApplicationAggregate
from ledger.domain.errors import DomainError
from ledger.schema.events import HumanReviewRequested


class OrchestratorState(TypedDict):
    application_id: str
    session_id: str
    credit_result: dict | None
    fraud_result: dict | None
    compliance_result: dict | None
    contributing_sessions: list[str] | None
    model_versions: dict[str, str] | None
    recommendation: str | None
    confidence: float | None
    approved_amount: float | None
    executive_summary: str | None
    conditions: list[str] | None
    key_risks: list[str] | None
    hard_constraints_applied: list[str] | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None


class DecisionOrchestratorAgent(BaseApexAgent):
    """
    Synthesises all prior agent outputs into a final recommendation.
    The only agent that reads from multiple aggregate streams before deciding.
    """

    def build_graph(self):
        g = StateGraph(OrchestratorState)
        g.add_node("validate_inputs", self._node_validate_inputs)
        g.add_node("load_credit_result", self._node_load_credit)
        g.add_node("load_fraud_result", self._node_load_fraud)
        g.add_node("load_compliance_result", self._node_load_compliance)
        g.add_node("synthesize_decision", self._node_synthesize)
        g.add_node("apply_hard_constraints", self._node_constraints)
        g.add_node("write_output", self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs", "load_credit_result")
        g.add_edge("load_credit_result", "load_fraud_result")
        g.add_edge("load_fraud_result", "load_compliance_result")
        g.add_edge("load_compliance_result", "synthesize_decision")
        g.add_edge("synthesize_decision", "apply_hard_constraints")
        g.add_edge("apply_hard_constraints", "write_output")
        g.add_edge("write_output", END)
        return g.compile()

    def _initial_state(self, application_id: str) -> OrchestratorState:
        return OrchestratorState(
            application_id=application_id,
            session_id=self.session_id,
            credit_result=None,
            fraud_result=None,
            compliance_result=None,
            contributing_sessions=[],
            model_versions={},
            recommendation=None,
            confidence=None,
            approved_amount=None,
            executive_summary=None,
            conditions=[],
            key_risks=[],
            hard_constraints_applied=[],
            errors=[],
            output_events=[],
            next_agent=None,
        )

    async def _node_validate_inputs(self, state: OrchestratorState) -> OrchestratorState:
        t = time.time()
        app_id = state["application_id"]
        errors: list[str] = []
        app = await LoanApplicationAggregate.load(self.store, app_id)
        comp_events = await self.store.load_stream(f"compliance-{app_id}")

        try:
            app.require_state(ApplicationState.PENDING_DECISION)
        except DomainError as exc:
            errors.append(str(exc))

        if any(
            e.get("event_type") == "ComplianceRuleFailed"
            and e.get("payload", {}).get("is_hard_block")
            for e in comp_events
        ):
            errors.append("Compliance hard block present; decision orchestration is not allowed")

        ms = int((time.time() - t) * 1000)
        if errors:
            await self._record_input_failed([], errors)
            raise ValueError(f"Input validation failed: {errors}")

        await self._record_input_validated(
            ["application_id", "pending_decision", "no_hard_block"],
            ms,
        )
        await self._record_node_execution(
            "validate_inputs",
            ["application_id"],
            ["application_ready"],
            ms,
        )
        return {**state, "errors": errors}

    async def _node_load_credit(self, state: OrchestratorState) -> OrchestratorState:
        t = time.time()
        app_id = state["application_id"]
        credit_events = await self.store.load_stream(f"credit-{app_id}")
        completed = [e for e in credit_events if e.get("event_type") == "CreditAnalysisCompleted"]
        if not completed:
            raise ValueError("Missing CreditAnalysisCompleted for orchestrator input")
        payload = completed[-1]["payload"]

        ms = int((time.time() - t) * 1000)
        await self._record_tool_call(
            "load_event_store_stream",
            f"stream_id=credit-{app_id}",
            "Loaded latest CreditAnalysisCompleted",
            ms,
        )
        await self._record_node_execution(
            "load_credit_result",
            ["credit_stream"],
            ["credit_result"],
            ms,
        )
        return {
            **state,
            "credit_result": payload,
            "contributing_sessions": [payload.get("session_id")] if payload.get("session_id") else [],
            "model_versions": {"credit_analysis": payload.get("model_version", "unknown")},
        }

    async def _node_load_fraud(self, state: OrchestratorState) -> OrchestratorState:
        t = time.time()
        app_id = state["application_id"]
        fraud_events = await self.store.load_stream(f"fraud-{app_id}")
        completed = [e for e in fraud_events if e.get("event_type") == "FraudScreeningCompleted"]
        if not completed:
            raise ValueError("Missing FraudScreeningCompleted for orchestrator input")
        payload = completed[-1]["payload"]

        sessions = list(state.get("contributing_sessions") or [])
        if payload.get("session_id") and payload["session_id"] not in sessions:
            sessions.append(payload["session_id"])
        models = dict(state.get("model_versions") or {})
        models["fraud_detection"] = payload.get("screening_model_version", "unknown")

        ms = int((time.time() - t) * 1000)
        await self._record_tool_call(
            "load_event_store_stream",
            f"stream_id=fraud-{app_id}",
            "Loaded latest FraudScreeningCompleted",
            ms,
        )
        await self._record_node_execution(
            "load_fraud_result",
            ["fraud_stream"],
            ["fraud_result"],
            ms,
        )
        return {
            **state,
            "fraud_result": payload,
            "contributing_sessions": sessions,
            "model_versions": models,
        }

    async def _node_load_compliance(self, state: OrchestratorState) -> OrchestratorState:
        t = time.time()
        app_id = state["application_id"]
        comp_events = await self.store.load_stream(f"compliance-{app_id}")
        completed = [e for e in comp_events if e.get("event_type") == "ComplianceCheckCompleted"]
        if not completed:
            raise ValueError("Missing ComplianceCheckCompleted for orchestrator input")
        payload = completed[-1]["payload"]
        initiated = next(
            (e.get("payload", {}) for e in comp_events if e.get("event_type") == "ComplianceCheckInitiated"),
            {},
        )

        sessions = list(state.get("contributing_sessions") or [])
        comp_session_id = payload.get("session_id") or initiated.get("session_id")
        if comp_session_id and comp_session_id not in sessions:
            sessions.append(comp_session_id)
        models = dict(state.get("model_versions") or {})
        models["compliance"] = initiated.get("regulation_set_version", "unknown")

        ms = int((time.time() - t) * 1000)
        await self._record_tool_call(
            "load_event_store_stream",
            f"stream_id=compliance-{app_id}",
            "Loaded latest ComplianceCheckCompleted",
            ms,
        )
        await self._record_node_execution(
            "load_compliance_result",
            ["compliance_stream"],
            ["compliance_result"],
            ms,
        )
        return {
            **state,
            "compliance_result": payload,
            "contributing_sessions": sessions,
            "model_versions": models,
        }

    async def _node_synthesize(self, state: OrchestratorState) -> OrchestratorState:
        t = time.time()
        credit = state.get("credit_result") or {}
        fraud = state.get("fraud_result") or {}
        compliance = state.get("compliance_result") or {}

        fallback = self._fallback_synthesis(state)
        decision = fallback
        ti = to = 0
        cost = 0.0

        system = """You are the Apex decision orchestrator.
Synthesize credit, fraud, and compliance findings into a lending recommendation.
Return ONLY JSON:
{
  "recommendation": "APPROVE" | "DECLINE" | "REFER",
  "confidence": <float 0.0-1.0>,
  "approved_amount_usd": <integer or null>,
  "conditions": ["condition"],
  "executive_summary": "<3-5 sentence executive summary>",
  "key_risks": ["risk"]
}
Do not invent missing source evidence."""

        user = json.dumps(
            {
                "credit": credit,
                "fraud": fraud,
                "compliance": compliance,
            },
            default=str,
        )
        try:
            content, ti, to, cost = await self._call_llm(system, user, max_tokens=1024)
            parsed = self._parse_json(content)
            decision = {
                "recommendation": str(parsed.get("recommendation", fallback["recommendation"])).upper(),
                "confidence": max(0.0, min(1.0, float(parsed.get("confidence", fallback["confidence"])))),
                "approved_amount_usd": parsed.get("approved_amount_usd", fallback["approved_amount_usd"]),
                "conditions": list(parsed.get("conditions", fallback["conditions"])),
                "executive_summary": parsed.get("executive_summary", fallback["executive_summary"]),
                "key_risks": list(parsed.get("key_risks", fallback["key_risks"])),
            }
        except Exception:
            decision = fallback

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "synthesize_decision",
            ["credit_result", "fraud_result", "compliance_result"],
            ["recommendation", "confidence", "approved_amount"],
            ms,
            ti,
            to,
            cost,
        )
        return {
            **state,
            "recommendation": decision["recommendation"],
            "confidence": decision["confidence"],
            "approved_amount": decision["approved_amount_usd"],
            "conditions": decision["conditions"],
            "executive_summary": decision["executive_summary"],
            "key_risks": decision["key_risks"],
        }

    def _fallback_synthesis(self, state: OrchestratorState) -> dict:
        credit = state.get("credit_result") or {}
        credit_decision = credit.get("decision", {}) or {}
        fraud = state.get("fraud_result") or {}
        compliance = state.get("compliance_result") or {}

        recommendation = "APPROVE"
        confidence = 0.82
        risks: list[str] = []
        conditions: list[str] = []
        approved_amount = credit_decision.get("recommended_limit_usd")

        risk_tier = str(credit_decision.get("risk_tier", "MEDIUM")).upper()
        fraud_score = float(fraud.get("fraud_score", 0.0))
        compliance_verdict = str(compliance.get("overall_verdict", "CLEAR")).upper()
        credit_confidence = float(credit_decision.get("confidence", 0.0))

        if risk_tier == "HIGH":
            recommendation = "DECLINE"
            confidence = 0.71
            risks.append("Credit analysis rated the applicant HIGH risk.")
        elif risk_tier == "MEDIUM":
            conditions.append("Quarterly covenant monitoring required.")
            confidence = 0.74

        if fraud_score >= 0.75:
            recommendation = "REFER"
            confidence = min(confidence, 0.55)
            risks.append("Fraud score exceeds the escalation threshold.")
        elif fraud_score >= 0.40:
            recommendation = "REFER"
            confidence = min(confidence, 0.62)
            risks.append("Fraud findings require human review before binding action.")

        if compliance_verdict == "BLOCKED":
            recommendation = "DECLINE"
            confidence = 0.95
            risks.append("Compliance verdict is BLOCKED.")

        if credit_confidence < 0.60:
            recommendation = "REFER"
            confidence = min(confidence, credit_confidence)
            risks.append("Credit confidence is below the referral floor.")

        summary = (
            f"Credit tier is {risk_tier} with fraud score {fraud_score:.0%}. "
            f"Compliance verdict is {compliance_verdict}. "
            f"Recommended action is {recommendation}."
        )
        return {
            "recommendation": recommendation,
            "confidence": confidence,
            "approved_amount_usd": approved_amount,
            "conditions": conditions,
            "executive_summary": summary,
            "key_risks": risks or ["No material risk drivers identified beyond standard policy review."],
        }

    async def _node_constraints(self, state: OrchestratorState) -> OrchestratorState:
        t = time.time()
        recommendation = state.get("recommendation") or "REFER"
        confidence = float(state.get("confidence") or 0.0)
        fraud_score = float((state.get("fraud_result") or {}).get("fraud_score", 0.0))
        constraints = list(state.get("hard_constraints_applied") or [])
        conditions = list(state.get("conditions") or [])
        key_risks = list(state.get("key_risks") or [])

        if confidence < 0.60 and recommendation != "REFER":
            recommendation = "REFER"
            constraints.append("CONFIDENCE_FLOOR")
            key_risks.append("Recommendation forced to REFER because confidence is below 0.60.")
        if fraud_score >= 0.75 and recommendation != "REFER":
            recommendation = "REFER"
            constraints.append("HIGH_FRAUD_ESCALATION")
            key_risks.append("Recommendation forced to REFER due to high fraud score.")
        if recommendation == "REFER" and "Human loan officer review required." not in conditions:
            conditions.append("Human loan officer review required.")

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "apply_hard_constraints",
            ["recommendation", "confidence", "fraud_result"],
            ["recommendation", "hard_constraints_applied"],
            ms,
        )
        return {
            **state,
            "recommendation": recommendation,
            "confidence": confidence,
            "conditions": conditions,
            "key_risks": key_risks,
            "hard_constraints_applied": constraints,
        }

    async def _node_write_output(self, state: OrchestratorState) -> OrchestratorState:
        t = time.time()
        app_id = state["application_id"]
        command = {
            "application_id": app_id,
            "orchestrator_session_id": self.session_id,
            "agent_type": "decision_orchestrator",
            "recommendation": state.get("recommendation"),
            "confidence": state.get("confidence"),
            "approved_amount_usd": (
                Decimal(str(state["approved_amount"])) if state.get("approved_amount") is not None else None
            ),
            "conditions": state.get("conditions") or [],
            "executive_summary": state.get("executive_summary") or "",
            "key_risks": state.get("key_risks") or [],
            "contributing_sessions": state.get("contributing_sessions") or [],
            "model_versions": state.get("model_versions") or {},
            "model_version": self.model,
            "causation_id": self.session_id,
        }
        events = await handle_decision_generated(self.store, command)

        if (state.get("recommendation") or "").upper() == "REFER":
            loan_events = await self.store.load_stream(f"loan-{app_id}")
            decision_event_id = None
            for event in reversed(loan_events):
                if event.get("event_type") == "DecisionGenerated":
                    payload = event.get("payload", {})
                    if payload.get("orchestrator_session_id") == self.session_id:
                        decision_event_id = event.get("event_id") or self.session_id
                        break
            review_event = HumanReviewRequested(
                application_id=app_id,
                reason="REFER recommendation requires human binding decision.",
                decision_event_id=str(decision_event_id or self.session_id),
                assigned_to=None,
                requested_at=datetime.now(),
            ).to_store_dict()
            positions = await self._append_with_retry(
                f"loan-{app_id}",
                [review_event],
                causation_id=self.session_id,
            )
            events.append(review_event)
            extra_event = {
                "stream_id": f"loan-{app_id}",
                "event_type": "HumanReviewRequested",
                "stream_position": positions[0] if positions else -1,
            }
        else:
            extra_event = None

        loan_events = await self.store.load_stream(f"loan-{app_id}")
        decision_position = -1
        for event in reversed(loan_events):
            if event.get("event_type") == "DecisionGenerated":
                payload = event.get("payload", {})
                if payload.get("orchestrator_session_id") == self.session_id:
                    decision_position = event.get("stream_position", -1)
                    break

        events_written = [
            {
                "stream_id": f"loan-{app_id}",
                "event_type": "DecisionGenerated",
                "stream_position": decision_position,
            }
        ]
        if extra_event:
            events_written.append(extra_event)

        await self._record_output_written(
            events_written,
            f"Decision generated: {state.get('recommendation')} at {float(state.get('confidence') or 0.0):.0%} confidence.",
        )
        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "write_output",
            ["recommendation", "confidence", "contributing_sessions"],
            ["events_written"],
            ms,
        )
        return {
            **state,
            "output_events": events_written,
            "next_agent": None if (state.get("recommendation") or "").upper() == "REFER" else "human_review",
            "next_agent_triggered": None,
        }
