from __future__ import annotations

import json

import pytest

from ledger.agents.extraction_api_client import DocumentExtractionApiClient


class FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_extraction_api_client_parses_nested_facts(monkeypatch):
    client = DocumentExtractionApiClient(
        base_url="https://extractor.example.com",
        api_key="secret",
        endpoint="/extract",
    )

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://extractor.example.com/extract"
        assert request.headers["Authorization"] == "Bearer secret"
        return FakeHttpResponse(
            {
                "result": {
                    "facts": {
                        "total_revenue": "123456",
                        "net_income": "12345",
                    }
                }
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    facts = await client.extract_financial_facts(
        file_path="/tmp/income.pdf",
        document_kind="income_statement",
        application_id="APP-123",
    )

    assert facts["total_revenue"] == "123456"
    assert facts["net_income"] == "12345"
