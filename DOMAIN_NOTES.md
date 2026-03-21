# Domain Notes — The Ledger (Phase 0)

## Purpose
The Ledger is an event-sourced system for loan decisioning. Every business fact is captured as an immutable event in a PostgreSQL append-only store. All downstream views (projections) are derived asynchronously from the event log.

## Event Sourcing vs Event-Driven Architecture
- Event Sourcing (ES) is the **source of truth**: state is reconstructed by replaying events. The `events` table is authoritative.
- Event-Driven Architecture (EDA) is **integration style**: events are published to trigger downstream actions, but state may still live in mutable tables.
- This system is ES-first. EDA is optional (e.g., outbox) but does not replace the event log.

## Aggregate Boundaries and Rationale
Aggregates exist to prevent inconsistent writes and to localize concurrency.

- **LoanApplication** (`loan-{application_id}`)
  Owns the application lifecycle state machine and the final decision events.

- **DocumentPackage** (`docpkg-{application_id}`)
  Owns document ingestion and extraction events. Separating this reduces contention during document processing.

- **AgentSession** (`agent-{agent_type}-{session_id}`)
  Owns agent execution trace events. This stream is the Gas Town memory anchor.

- **CreditRecord** (`credit-{application_id}`)
  Owns credit analysis events and historical profile consumption.

- **FraudScreening** (`fraud-{application_id}`)
  Owns fraud assessment events and anomalies.

- **ComplianceRecord** (`compliance-{application_id}`)
  Owns deterministic regulatory checks and overall compliance verdict.

- **AuditLedger** (`audit-{entity_id}`)
  Owns integrity checks and tamper-evidence chains.

## Why ComplianceRecord Is Separate from LoanApplication
Compliance checks are deterministic, read-heavy, and can be run independently of other agents. If Compliance events were written directly into the LoanApplication aggregate, any compliance re-check would contend on the loan stream, increasing OCC collisions when other agents append concurrently. A dedicated ComplianceRecord aggregate isolates those writes, allowing compliance to execute and complete without blocking credit/fraud agents or the decision orchestrator. The LoanApplication aggregate consumes the final ComplianceCheckCompleted event as an input to decisioning, preserving consistency without hot-spotting the loan stream.

## Gas Town Pattern (AgentSession Aggregate)
- Every agent session **must** start with `AgentSessionStarted`.
- Session streams are replayable to reconstruct agent context after a crash.
- The base agent enforces this ordering rule at append time.

## Read vs Write Side
- **Write side**: commands append events to the event store.
- **Read side**: projections are built asynchronously by a projection daemon using global ordering.
- This separation keeps API writes low-latency and allows projection replays for temporal queries.

## Operational Constraints
- The `applicant_registry` schema is read-only from the runtime system.
- All application-specific state changes must be captured as events in the ledger.
