# Domain Notes — The Ledger (Phase 0)

## Purpose
The Ledger is an event-sourced system for loan decisioning. Every business fact is captured as an immutable event in a PostgreSQL append-only store. All downstream views (projections) are derived asynchronously from the event log.

## Event Sourcing vs Event-Driven Architecture
- Event Sourcing (ES) is the **source of truth**: state is reconstructed by replaying events. The `events` table is authoritative.
- Event-Driven Architecture (EDA) is **integration style**: events are published to trigger downstream actions, but state may still live in mutable tables.
- This system is ES-first. EDA is optional (e.g., outbox) but does not replace the event log.
- Redesigning LangChain traces as ES improves reconstruction: agent traces become deterministic, replayable streams instead of ephemeral logs, enabling crash recovery and auditability.

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

## Concurrency: Two Agents Append With expected_version=3
1. Both agents load stream version 3.
2. Agent A acquires the `event_streams` row lock first, passes OCC, appends events at position 4, updates current_version to 4, commits.
3. Agent B then acquires the lock, sees current_version=4, and raises OptimisticConcurrencyError because expected_version=3.
4. Result: exactly one success, no duplicate event at position 4.

## Projection Lag (200ms Read After Write)
Use read-your-writes via the write model when strict freshness is required:
- The write API returns the appended event or the new version/limit.
- The UI can render that authoritative response immediately while projections catch up.
- For strict read models, support an `as_of_position` query to replay to the latest known position.

## Upcasting Strategy (CreditDecisionMade)
No `CreditDecisionMade` event exists in this codebase; the closest analog is `DecisionGenerated` or `CreditAnalysisCompleted`.
If a legacy `CreditDecisionMade` is introduced, an upcaster should:
- Map fields into the current event type payload.
- Fill missing fields with safe defaults (e.g., `model_versions={}`).
- Keep an `original_event_type` marker in metadata for audit.

## Marten Parallel Projection Analogy (Python)
Emulate Marten-style parallel projection execution by:
- Partitioning by stream_id hash or aggregate type.
- Running multiple async workers, each reading from `load_all()` and committing checkpoints.
- Using `projection_checkpoints` for idempotent resume and at-least-once processing.

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
