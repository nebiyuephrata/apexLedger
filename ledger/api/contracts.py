from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class ApiError(BaseModel):
    error_type: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str | None = None
    request_id: str | None = None


class ApiMeta(BaseModel):
    request_id: str
    idempotency_key: str | None = None
    idempotent_replay: bool = False
    latency_ms: int | None = None


class ApiEnvelope(BaseModel):
    ok: bool
    result: Any | None = None
    error: ApiError | None = None
    meta: ApiMeta | None = None


@dataclass
class RouteMetricSnapshot:
    count: int = 0
    latencies_ms: list[int] = field(default_factory=list)

    def add(self, latency_ms: int) -> None:
        self.count += 1
        self.latencies_ms.append(latency_ms)
        if len(self.latencies_ms) > 500:
            self.latencies_ms = self.latencies_ms[-500:]

    def summary(self) -> dict[str, int]:
        if not self.latencies_ms:
            return {"count": self.count, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0}
        ordered = sorted(self.latencies_ms)
        def percentile(p: float) -> int:
            idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
            return int(ordered[idx])
        return {
            "count": self.count,
            "p50_ms": percentile(0.50),
            "p95_ms": percentile(0.95),
            "p99_ms": percentile(0.99),
        }
