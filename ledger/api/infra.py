from __future__ import annotations

import hashlib
import json
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Awaitable, Callable

from .contracts import RouteMetricSnapshot

try:
    from redis import asyncio as redis_async
except Exception:  # pragma: no cover
    redis_async = None


request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


@dataclass
class IdempotencyRecord:
    fingerprint: str
    response: dict
    status_code: int


@dataclass
class RuntimeMetrics:
    cache_hits: int = 0
    cache_misses: int = 0
    cache_invalidations: int = 0
    db_queries: int = 0
    db_total_latency_ms: int = 0
    action_logs: list[dict[str, Any]] = field(default_factory=list)
    route_metrics: dict[str, RouteMetricSnapshot] = field(default_factory=dict)

    def record_cache(self, hit: bool) -> None:
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def record_db_query(self, latency_ms: int) -> None:
        self.db_queries += 1
        self.db_total_latency_ms += latency_ms

    def record_route(self, route_name: str, latency_ms: int) -> None:
        snapshot = self.route_metrics.setdefault(route_name, RouteMetricSnapshot())
        snapshot.add(latency_ms)

    def record_action(self, entry: dict[str, Any]) -> None:
        self.action_logs.append(entry)
        if len(self.action_logs) > 200:
            self.action_logs = self.action_logs[-200:]

    def snapshot(self) -> dict[str, Any]:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_invalidations": self.cache_invalidations,
            "db_queries": self.db_queries,
            "avg_db_latency_ms": int(self.db_total_latency_ms / self.db_queries) if self.db_queries else 0,
            "routes": {name: metric.summary() for name, metric in self.route_metrics.items()},
            "recent_actions": self.action_logs[-20:],
        }


class InMemoryInfraStore:
    def __init__(self) -> None:
        self._values: dict[str, tuple[Any, float | None]] = {}
        self._buckets: dict[str, tuple[int, float]] = {}

    async def get_json(self, key: str) -> Any | None:
        entry = self._values.get(key)
        if not entry:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= time.time():
            self._values.pop(key, None)
            return None
        return value

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        self._values[key] = (value, expires_at)

    async def delete_pattern(self, pattern: str) -> None:
        for key in list(self._values.keys()):
            if fnmatch(key, pattern):
                self._values.pop(key, None)

    async def increment_window(self, key: str, window_seconds: int) -> int:
        count, expires_at = self._buckets.get(key, (0, time.time() + window_seconds))
        now = time.time()
        if expires_at <= now:
            count, expires_at = 0, now + window_seconds
        count += 1
        self._buckets[key] = (count, expires_at)
        return count


class RedisInfraStore:
    def __init__(self, url: str) -> None:
        if redis_async is None:
            raise RuntimeError("redis package is not available")
        self._client = redis_async.from_url(url, encoding="utf-8", decode_responses=True)

    async def get_json(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        encoded = json.dumps(value, default=str)
        if ttl_seconds:
            await self._client.set(key, encoded, ex=ttl_seconds)
        else:
            await self._client.set(key, encoded)

    async def delete_pattern(self, pattern: str) -> None:
        cursor = 0
        while True:
            cursor, keys = await self._client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                await self._client.delete(*keys)
            if cursor == 0:
                break

    async def increment_window(self, key: str, window_seconds: int) -> int:
        async with self._client.pipeline(transaction=True) as pipe:
            value = await pipe.incr(key).expire(key, window_seconds, nx=True).execute()
        return int(value[0])


_store: InMemoryInfraStore | RedisInfraStore | None = None
metrics = RuntimeMetrics()


def get_infra_store() -> InMemoryInfraStore | RedisInfraStore:
    global _store
    if _store is not None:
        return _store
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        try:
            _store = RedisInfraStore(redis_url)
            return _store
        except Exception:
            pass
    _store = InMemoryInfraStore()
    return _store


def stable_fingerprint(payload: dict, scope: str) -> str:
    encoded = json.dumps({"scope": scope, "payload": payload}, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def get_cached_or_load(key: str, ttl_seconds: int, loader: Callable[[], Awaitable[Any]]) -> Any:
    store = get_infra_store()
    cached = await store.get_json(key)
    if cached is not None:
        metrics.record_cache(hit=True)
        return cached
    metrics.record_cache(hit=False)
    value = await loader()
    await store.set_json(key, value, ttl_seconds=ttl_seconds)
    return value


async def enforce_rate_limit(scope: str, budget: int, window_seconds: int) -> None:
    store = get_infra_store()
    count = await store.increment_window(scope, window_seconds)
    if count > budget:
        raise ValueError(f"Rate limit exceeded for {scope}: {count}>{budget}")


async def load_idempotency_record(key: str) -> IdempotencyRecord | None:
    store = get_infra_store()
    record = await store.get_json(key)
    if not record:
        return None
    return IdempotencyRecord(
        fingerprint=str(record["fingerprint"]),
        response=record["response"],
        status_code=int(record["status_code"]),
    )


async def save_idempotency_record(key: str, fingerprint: str, response: dict, status_code: int, ttl_seconds: int) -> None:
    store = get_infra_store()
    await store.set_json(
        key,
        {
            "fingerprint": fingerprint,
            "response": response,
            "status_code": status_code,
        },
        ttl_seconds=ttl_seconds,
    )


async def invalidate_patterns(*patterns: str) -> None:
    store = get_infra_store()
    for pattern in patterns:
        metrics.cache_invalidations += 1
        await store.delete_pattern(pattern)


def record_db_query(latency_ms: int) -> None:
    metrics.record_db_query(latency_ms)


def record_route_latency(route_name: str, latency_ms: int) -> None:
    metrics.record_route(route_name, latency_ms)


def record_action(entry: dict[str, Any]) -> None:
    metrics.record_action(entry)


def runtime_snapshot() -> dict[str, Any]:
    return metrics.snapshot()
