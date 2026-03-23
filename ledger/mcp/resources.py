from __future__ import annotations

import os
from datetime import datetime
import asyncpg

from ledger.event_store import EventStore
from ledger.projections.application_summary import ApplicationSummaryProjection
from ledger.projections.compliance_audit import ComplianceAuditProjection
from ledger.projections.agent_performance import AgentPerformanceProjection
from ledger.projections.daemon import ProjectionDaemon
from ledger import upcasters

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:apex@localhost:55432/apex_ledger")


async def get_application_summary(application_id: str) -> dict | None:
    conn = await asyncpg.connect(DB_URL)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM projection_application_summary WHERE application_id=$1",
            application_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def get_compliance_view(application_id: str, as_of: str | None = None) -> dict | None:
    proj = ComplianceAuditProjection()
    conn = await asyncpg.connect(DB_URL)
    try:
        if as_of:
            ts = datetime.fromisoformat(as_of)
            return await proj.get_compliance_at(conn, application_id, ts)
        row = await conn.fetchrow(
            "SELECT * FROM projection_compliance_audit WHERE application_id=$1 ORDER BY recorded_at DESC LIMIT 1",
            application_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def get_audit_trail(application_id: str) -> list[dict]:
    store = EventStore(DB_URL, upcaster_registry=upcasters.registry)
    upcasters.registry.store = store
    await store.connect()
    try:
        return await store.load_stream(f"audit-{application_id}")
    finally:
        await store.close()


async def get_agent_performance(agent_id: str) -> list[dict]:
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch(
            "SELECT * FROM projection_agent_performance WHERE agent_type=$1",
            agent_id,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def get_agent_session(agent_id: str, session_id: str) -> list[dict]:
    store = EventStore(DB_URL, upcaster_registry=upcasters.registry)
    upcasters.registry.store = store
    await store.connect()
    try:
        return await store.load_stream(f"agent-{agent_id}-{session_id}")
    finally:
        await store.close()


async def get_health() -> dict:
    daemon = ProjectionDaemon(DB_URL, [
        ApplicationSummaryProjection(),
        AgentPerformanceProjection(),
        ComplianceAuditProjection(),
    ])
    lags = {}
    for name in ["application_summary", "agent_performance", "compliance_audit"]:
        lags[name] = await daemon.get_lag(name)
    await daemon.close()
    return {"lags": lags}
