# The Ledger — Event-Sourced System of Record for AI Agents

This repository implements an event-sourced, CQRS-based ledger for multi-agent AI decisioning. The write side is an append-only event store with optimistic concurrency control (OCC). The read side is a projection daemon that builds read-optimized tables for operational dashboards, analytics, and compliance audits. Every agent action is recorded in an AgentSession stream (Gas Town pattern) for crash recovery and forensic auditability.

## Quick Start
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start PostgreSQL (docker)
docker run -d -e POSTGRES_PASSWORD=apex -e POSTGRES_DB=apex_ledger -p 5432:5432 postgres:16

# 3. Set environment (the .env file is git-ignored)
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY and DATABASE_URL

# 4. Generate all data (companies + documents + seed events → DB)
python datagen/generate_all.py --db-url postgresql://postgres:apex@localhost/apex_ledger

# 5. Validate schema (no DB needed)
python datagen/generate_all.py --skip-db --skip-docs --validate-only

# 6. Run Phase 0 tests (must pass before starting Phase 1)
pytest tests/test_schema_and_generator.py -v
```

## Architecture Overview
- Write Side: `ledger/event_store.py` provides append-only event storage with OCC and an outbox for reliable delivery.
- Read Side: `ledger/projections/daemon.py` consumes the global stream and builds read models.
- Aggregates: `ledger/domain/aggregates/` reconstruct state purely by replaying events.
- Agents: `ledger/agents/` contains one file per agent plus shared base scaffolding.
- Registry: `ledger/registry/client.py` is read-only access to the applicant registry.

## Repository Map
- `ledger/schema/events.py` Event catalogue and typed models.
- `ledger/event_store.py` Async event store + InMemoryEventStore for tests.
- `ledger/domain/aggregates/loan_application.py` Loan aggregate state machine and invariants.
- `ledger/domain/aggregates/agent_session.py` Agent session aggregate and Gas Town guards.
- `ledger/commands/handlers.py` Command handlers following the four-step CQRS pattern.
- `ledger/agents/credit_analysis_agent.py` Reference agent implementation.
- `ledger/agents/document_processing_agent.py` Document ingestion agent.
- `ledger/agents/fraud_detection_agent.py` Fraud agent stub.
- `ledger/agents/compliance_agent.py` Compliance agent stub.
- `ledger/agents/decision_orchestrator_agent.py` Orchestrator stub.
- `ledger/projections/` ApplicationSummary, AgentPerformanceLedger, ComplianceAuditView, and daemon.
- `tests/` Phase gate tests.

## What Works Out of the Box
- Full event schema and registry.
- Data generator for companies, documents, and seed events.
- Event simulator for end-to-end agent pipelines.
- Schema validator against `EVENT_REGISTRY`.

## What You Implement (by phase)
| Component | File | Phase |
|-----------|------|-------|
| EventStore | `ledger/event_store.py` | 1 |
| ApplicantRegistryClient | `ledger/registry/client.py` | 1 |
| Domain aggregates | `ledger/domain/aggregates/` | 2 |
| DocumentProcessingAgent | `ledger/agents/document_processing_agent.py` | 2 |
| CreditAnalysisAgent | `ledger/agents/credit_analysis_agent.py` | 2 |
| FraudDetectionAgent | `ledger/agents/fraud_detection_agent.py` | 3 |
| ComplianceAgent | `ledger/agents/compliance_agent.py` | 3 |
| DecisionOrchestratorAgent | `ledger/agents/decision_orchestrator_agent.py` | 3 |
| Projections + daemon | `ledger/projections/` | 4 |
| Upcasters | `ledger/upcasters.py` | 4 |
| MCP server | `ledger/mcp_server.py` | 5 |

## Gate Tests by Phase
```bash
pytest tests/test_schema_and_generator.py -v  # Phase 0
pytest tests/test_event_store.py -v           # Phase 1
pytest tests/test_domain.py -v               # Phase 2
pytest tests/test_narratives.py -v           # Phase 3
pytest tests/test_projections.py -v          # Phase 4
pytest tests/test_mcp.py -v                  # Phase 5
```
