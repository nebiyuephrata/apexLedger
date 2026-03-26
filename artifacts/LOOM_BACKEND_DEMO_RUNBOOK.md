# Backend Demo Runbook

## Command

Run the full backend-only demo:

```bash
./scripts/demo_backend_rubrics.sh
```

Run a single rubric section:

```bash
uv run pytest tests/test_demo_showcase.py::test_demo_week_standard -q -s
uv run pytest tests/test_demo_showcase.py::test_demo_concurrency_pressure -q -s
uv run pytest tests/test_demo_showcase.py::test_demo_temporal_compliance_query -q -s
uv run pytest tests/test_demo_showcase.py::test_demo_upcasting_immutability -q -s
uv run pytest tests/test_demo_showcase.py::test_demo_gas_town_recovery -q -s
```

## Recording Flow

Keep the video under 6 minutes. Open one terminal, zoom in the font, and run the full script once.

## What To Say

### 1. Complete Decision History

Say:

```text
I’m starting with the core deliverable for the week: show me the complete decision history of one application from first event to final approval.

This output is coming from the backend only. I’m creating a fresh application, starting agent sessions, recording credit, fraud, compliance, and human review, and then querying the full event history.

The important thing to notice is that every category is visible in one timeline: submission, agent actions, compliance checks, decision generation, human review, and final approval.

I’m also showing the causal evidence beside each event. Where correlation and causation metadata exist, you can see them directly. Where the workflow uses payload-level causality, you can see the triggering session IDs and contributing sessions.
```

When the integrity check prints, say:

```text
Now I’m running the cryptographic integrity check on the same application. This proves the event history has not been tampered with. The result is visible immediately as chain_valid true and tamper_detected false.
```

### 2. Concurrency Under Pressure

Say:

```text
Next I’m demonstrating optimistic concurrency control.

Two tasks are trying to append to the same stream at the same expected version. Exactly one should win, and the other should get a structured OptimisticConcurrencyError.

The key thing to watch is that the final stream length stays correct. We should end with exactly one new event, not two duplicates.
```

When the output prints, say:

```text
You can see one task succeeded, one failed with expected versus actual version, and then I reload the stream version to show what the losing task would retry against.
```

### 3. Temporal Compliance Query

Say:

```text
This section proves regulatory time travel.

I’m writing compliance events at specific historical timestamps, then asking for the compliance state at a meaningful point in the past and comparing it to the current state.

The two answers should be different. That demonstrates the system is returning point-in-time state, not just the latest row.
```

When the output prints, say:

```text
The past state is before the hard block is completed, while the current state is blocked. That difference is the evidence that the temporal query is working correctly.
```

### 4. Upcasting and Immutability

Say:

```text
Now I’m showing schema evolution without mutating history.

I write a v1 credit analysis event, then I load it through the EventStore interface. The store returns a v2 event because the upcaster adds the newer fields at read time.

Then I query the raw database row directly. The important thing is that the stored payload is still unchanged in v1 form.
```

When both outputs are visible, say:

```text
This contrast is exactly what we want in a compliant event-sourced system: consumers see the current schema, but the database remains immutable.
```

### 5. Gas Town Recovery

Say:

```text
Finally, I’m simulating an agent crash and reconstructing memory from the event store.

I append a short agent session history, including a partial decision, then I discard the original in-memory context and rebuild the session state from events alone.

The reconstructed context should show the last completed action, pending work, and the session health status.
```

When the recovered context prints, say:

```text
The important field here is NEEDS_RECONCILIATION. That tells us the agent stopped after a partial decision and should not resume blindly. It has enough context to continue safely without repeating completed work.
```

## Close

Say:

```text
This is why event sourcing is required here, not just useful. We need immutable history for auditability, optimistic concurrency for competing agents, temporal queries for regulators, upcasting for long-lived schemas, and Gas Town recovery for resilient agent execution.
```
