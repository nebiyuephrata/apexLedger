"""
ledger/agents/runtime.py
========================
Shared runtime helpers for running Apex agents from scripts and MCP tools.
"""
from __future__ import annotations

import os
from typing import Any

import asyncpg
from anthropic import AsyncAnthropic

from ledger.agents.credit_analysis_agent import CreditAnalysisAgent
from ledger.agents.document_processing_agent import DocumentProcessingAgent
from ledger.agents.extraction_api_client import DocumentExtractionApiClient
from ledger.agents.fraud_detection_agent import FraudDetectionAgent
from ledger.agents.compliance_agent import ComplianceAgent
from ledger.agents.decision_orchestrator_agent import DecisionOrchestratorAgent
from ledger.registry.client import ApplicantRegistryClient


def build_llm_client() -> AsyncAnthropic | None:
    provider = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if provider == "gemini" or os.environ.get("GEMINI_API_KEY"):
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return AsyncAnthropic(api_key=api_key)


def build_extraction_client() -> DocumentExtractionApiClient | None:
    base_url = os.environ.get("DOCUMENT_EXTRACTION_API_BASE_URL")
    if not base_url:
        return None
    return DocumentExtractionApiClient(
        base_url=base_url,
        api_key=os.environ.get("DOCUMENT_EXTRACTION_API_KEY"),
        endpoint=os.environ.get("DOCUMENT_EXTRACTION_API_ENDPOINT", "/extract"),
        timeout_seconds=int(os.environ.get("DOCUMENT_EXTRACTION_TIMEOUT_SECONDS", "60")),
    )


async def build_registry_client(db_url: str) -> tuple[asyncpg.Pool, ApplicantRegistryClient]:
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=4)
    return pool, ApplicantRegistryClient(pool)


async def run_credit_analysis_agent(
    *,
    store,
    registry,
    application_id: str,
    agent_id: str = "agent-credit-1",
    model: str = "claude-sonnet-4-20250514",
    client: Any | None = None,
    session_id: str | None = None,
    context_source: str = "fresh",
) -> dict:
    agent = CreditAnalysisAgent(
        agent_id=agent_id,
        agent_type="credit_analysis",
        store=store,
        registry=registry,
        client=client,
        model=model,
    )
    result = await agent.process_application(
        application_id,
        session_id=session_id,
        context_source=context_source,
    )
    return {
        "agent_type": "credit_analysis",
        "application_id": application_id,
        "session_id": agent.session_id,
        "session_stream": agent._session_stream,
        "result": result,
    }


async def run_document_processing_agent(
    *,
    store,
    registry,
    application_id: str,
    agent_id: str = "agent-document-1",
    model: str = "claude-sonnet-4-20250514",
    client: Any | None = None,
    session_id: str | None = None,
    context_source: str = "fresh",
    extraction_client: Any | None = None,
) -> dict:
    agent = DocumentProcessingAgent(
        agent_id=agent_id,
        agent_type="document_processing",
        store=store,
        registry=registry,
        client=client,
        model=model,
        extraction_client=extraction_client or build_extraction_client(),
    )
    result = await agent.process_application(
        application_id,
        session_id=session_id,
        context_source=context_source,
    )
    return {
        "agent_type": "document_processing",
        "application_id": application_id,
        "session_id": agent.session_id,
        "session_stream": agent._session_stream,
        "result": result,
    }


async def run_fraud_detection_agent(
    *,
    store,
    registry,
    application_id: str,
    agent_id: str = "agent-fraud-1",
    model: str = "claude-sonnet-4-20250514",
    client: Any | None = None,
    session_id: str | None = None,
    context_source: str = "fresh",
) -> dict:
    agent = FraudDetectionAgent(
        agent_id=agent_id,
        agent_type="fraud_detection",
        store=store,
        registry=registry,
        client=client,
        model=model,
    )
    result = await agent.process_application(
        application_id,
        session_id=session_id,
        context_source=context_source,
    )
    return {
        "agent_type": "fraud_detection",
        "application_id": application_id,
        "session_id": agent.session_id,
        "session_stream": agent._session_stream,
        "result": result,
    }


async def run_compliance_agent(
    *,
    store,
    registry,
    application_id: str,
    agent_id: str = "agent-compliance-1",
    model: str = "claude-sonnet-4-20250514",
    client: Any | None = None,
    session_id: str | None = None,
    context_source: str = "fresh",
) -> dict:
    agent = ComplianceAgent(
        agent_id=agent_id,
        agent_type="compliance",
        store=store,
        registry=registry,
        client=client,
        model=model,
    )
    result = await agent.process_application(
        application_id,
        session_id=session_id,
        context_source=context_source,
    )
    return {
        "agent_type": "compliance",
        "application_id": application_id,
        "session_id": agent.session_id,
        "session_stream": agent._session_stream,
        "result": result,
    }


async def run_decision_orchestrator_agent(
    *,
    store,
    registry,
    application_id: str,
    agent_id: str = "agent-orchestrator-1",
    model: str = "claude-sonnet-4-20250514",
    client: Any | None = None,
    session_id: str | None = None,
    context_source: str = "fresh",
) -> dict:
    agent = DecisionOrchestratorAgent(
        agent_id=agent_id,
        agent_type="decision_orchestrator",
        store=store,
        registry=registry,
        client=client,
        model=model,
    )
    result = await agent.process_application(
        application_id,
        session_id=session_id,
        context_source=context_source,
    )
    return {
        "agent_type": "decision_orchestrator",
        "application_id": application_id,
        "session_id": agent.session_id,
        "session_stream": agent._session_stream,
        "result": result,
    }
