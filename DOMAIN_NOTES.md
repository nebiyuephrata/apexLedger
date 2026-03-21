# Domain Notes — The Ledger

## Purpose
The Ledger is an event-sourced system of record for multi-agent loan decisioning. All business facts are immutable events stored in PostgreSQL. Aggregate state is rebuilt by replaying events, and read-optimized projections provide fast query views.

## Event Sourcing vs EDA
Event-Driven Architecture (EDA) traces are optional, lossy, and not authoritative. Event Sourcing (ES) makes events the permanent source of truth. In The Ledger, all decisions become immutable events in `events`, aggregates rebuild state from streams, and projections provide queryable views. This enables deterministic replay, auditability, and time-travel reconstruction.

## Aggregate Boundaries
Chosen boundaries in this implementation:
- LoanApplication (`loan-{application_id}`)
- DocumentPackage (`docpkg-{application_id}`)
- AgentSession (`agent-{agent_type}-{session_id}`)
- ComplianceRecord (`compliance-{application_id}`)
- CreditRecord (`credit-{application_id}`)
- FraudScreening (`fraud-{application_id}`)
- AuditLedger (`audit-{entity_id}`)

### Why ComplianceRecord Is Separate
Merging compliance into LoanApplication would create write contention: each compliance rule write would lock the loan stream and collide with other agents. A dedicated ComplianceRecord isolates compliance spikes and prevents compliance retries from blocking decisions.

## OCC Concurrency Sequence (expected_version=3)
1. Two agents load the stream at version 3.
2. Agent A acquires the row lock in `event_streams`, passes the OCC check, appends at position 4, and commits.
3. Agent B acquires the lock later, sees current_version=4, and raises `OptimisticConcurrencyError(stream_id, expected=3, actual=4)`.
4. Agent B reloads and decides whether to retry.

## Projection Lag Contract
Projections can lag writes by ~200ms. The UI should display a lag indicator based on `last_event_at`. For strict actions, the system can re-read from the event stream or wait until the projection checkpoint catches up.

## Upcasting Strategy (Current Implementation)
Upcasters run on read, never on write, and never fabricate data. The current registry applies:
- `CreditAnalysisCompleted` v1 → v2: adds `regulatory_basis=[]` if missing
- `DecisionGenerated` v1 → v2: adds `model_versions={}` if missing

Example pattern:
```python
if et == "CreditAnalysisCompleted" and ver < 2:
    p.setdefault("regulatory_basis", [])
if et == "DecisionGenerated" and ver < 2:
    p.setdefault("model_versions", {})
```

## Parallel Projection Scaling
To scale projections, use advisory locks or a lease table. Each worker claims a projection or event-range lease. If a worker dies, the lease expires and another worker resumes from the last checkpoint. This prevents double-processing and corrupted aggregates.

## Gas Town Pattern
Every agent session starts with `AgentSessionStarted` and optionally `AgentContextLoaded`. Session streams are replayable via `reconstruct_agent_context` to resume after crashes.

## Read vs Write
- Write side: command handlers append immutable events.
- Read side: projection daemon consumes the global stream and builds query tables.

## Data Boundary
`applicant_registry` is read-only. All application-specific facts live in ledger streams.
