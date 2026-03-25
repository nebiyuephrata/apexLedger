"""
ledger/agents/extraction_api_client.py
======================================
HTTP client adapter for an external document extraction service.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request


class DocumentExtractionApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        endpoint: str = "/extract",
        timeout_seconds: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self.timeout_seconds = timeout_seconds

    async def extract_financial_facts(
        self,
        *,
        file_path: str,
        document_kind: str,
        application_id: str,
    ) -> dict:
        def _request() -> dict:
            payload = {
                "file_path": file_path,
                "document_kind": document_kind,
                "application_id": application_id,
            }
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            request = urllib.request.Request(
                f"{self.base_url}{self.endpoint}",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                raise RuntimeError(f"Extraction API error {exc.code}: {detail}") from exc

            if isinstance(raw, dict):
                if isinstance(raw.get("facts"), dict):
                    return dict(raw["facts"])
                if isinstance(raw.get("result"), dict) and isinstance(raw["result"].get("facts"), dict):
                    return dict(raw["result"]["facts"])
                return raw
            raise RuntimeError(f"Unexpected extraction API response shape: {type(raw)!r}")

        return await asyncio.to_thread(_request)
