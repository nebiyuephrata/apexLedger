"""
ledger/projections/daemon.py
============================
Async projection daemon that polls the global event stream and updates projections.
"""
from __future__ import annotations
import asyncio
import logging
import json
from typing import Iterable

import asyncpg

logger = logging.getLogger(__name__)


class ProjectionDaemon:
    def __init__(
        self,
        db_url: str,
        projections: Iterable,
        poll_interval_ms: int = 100,
        batch_size: int = 200,
        max_retries: int = 3,
    ):
        self.db_url = db_url
        self.projections = list(projections)
        self.poll_interval_ms = poll_interval_ms
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._pool: asyncpg.Pool | None = None
        self._stop = False

    async def start(self):
        if not self._pool:
            self._pool = await asyncpg.create_pool(self.db_url, min_size=2, max_size=10)
        for p in self.projections:
            async with self._pool.acquire() as conn:
                await p.ensure_tables(conn)
        while not self._stop:
            await self.run_once()
            await asyncio.sleep(self.poll_interval_ms / 1000)

    def stop(self):
        self._stop = True

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _load_checkpoint(self, conn: asyncpg.Connection, projection_name: str) -> int:
        row = await conn.fetchrow(
            "SELECT last_position FROM projection_checkpoints WHERE projection_name=$1",
            projection_name,
        )
        return int(row["last_position"]) if row else 0

    async def _save_checkpoint(self, conn: asyncpg.Connection, projection_name: str, position: int) -> None:
        await conn.execute(
            "INSERT INTO projection_checkpoints(projection_name, last_position) "
            "VALUES($1, $2) "
            "ON CONFLICT (projection_name) DO UPDATE SET last_position=$2, updated_at=NOW()",
            projection_name, position,
        )

    async def get_lag(self, projection_name: str) -> dict[str, int]:
        if not self._pool:
            self._pool = await asyncpg.create_pool(self.db_url, min_size=1, max_size=2)
        async with self._pool.acquire() as conn:
            max_pos = int(await conn.fetchval("SELECT COALESCE(MAX(global_position),0) FROM events"))
            last = await self._load_checkpoint(conn, projection_name)
            latest_row = await conn.fetchrow(
                "SELECT global_position, recorded_at FROM events ORDER BY global_position DESC LIMIT 1"
            )
            processed_row = None
            if last > 0:
                processed_row = await conn.fetchrow(
                    "SELECT global_position, recorded_at FROM events WHERE global_position=$1",
                    last,
                )

            lag_ms = 0
            if latest_row and max_pos > last:
                latest_ts = latest_row["recorded_at"]
                if processed_row:
                    processed_ts = processed_row["recorded_at"]
                    lag_ms = max(0, int((latest_ts - processed_ts).total_seconds() * 1000))
                else:
                    lag_ms = max(0, int((self.poll_interval_ms or 0)))

            return {
                "lag_ms": lag_ms,
                "last_processed_position": int(last),
                "latest_position": max_pos,
                "position_delta": max(0, max_pos - int(last)),
            }

    async def run_once(self):
        if not self._pool:
            self._pool = await asyncpg.create_pool(self.db_url, min_size=2, max_size=10)
        for proj in self.projections:
            async with self._pool.acquire() as conn:
                await proj.ensure_tables(conn)
                last_pos = await self._load_checkpoint(conn, proj.name)
                rows = await conn.fetch(
                    "SELECT global_position, stream_id, stream_position, event_type, "
                    "event_version, payload, metadata, recorded_at "
                    "FROM events WHERE global_position > $1 "
                    "ORDER BY global_position ASC LIMIT $2",
                    last_pos, self.batch_size,
                )
                for row in rows:
                    event = dict(row)
                    if isinstance(event.get("payload"), str):
                        try:
                            event["payload"] = json.loads(event["payload"])
                        except Exception:
                            event["payload"] = {}
                    if isinstance(event.get("metadata"), str):
                        try:
                            event["metadata"] = json.loads(event["metadata"])
                        except Exception:
                            event["metadata"] = {}
                    retries = 0
                    while True:
                        try:
                            async with conn.transaction():
                                await proj.handle(event, conn)
                                await self._save_checkpoint(conn, proj.name, event["global_position"])
                            break
                        except Exception as e:
                            retries += 1
                            if retries > self.max_retries:
                                logger.exception(
                                    "Projection %s failed on event %s; skipping",
                                    proj.name, event.get("global_position"),
                                )
                                async with conn.transaction():
                                    await self._save_checkpoint(conn, proj.name, event["global_position"])
                                break
                            await asyncio.sleep(0.05 * retries)

    async def rebuild_from_scratch(self, projection_name: str):
        if not self._pool:
            self._pool = await asyncpg.create_pool(self.db_url, min_size=2, max_size=10)
        proj = next((p for p in self.projections if p.name == projection_name), None)
        if not proj:
            raise ValueError(f"Unknown projection: {projection_name}")
        async with self._pool.acquire() as conn:
            await proj.rebuild(conn)
            max_pos = await conn.fetchval("SELECT COALESCE(MAX(global_position),0) FROM events")
            await self._save_checkpoint(conn, proj.name, int(max_pos))
