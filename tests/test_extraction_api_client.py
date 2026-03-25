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
async def test_extraction_api_client_uses_ingest_and_structured_query(monkeypatch, tmp_path):
    client = DocumentExtractionApiClient(
        base_url="http://127.0.0.1:8000",
        api_key="secret",
    )
    source = tmp_path / "income_statement.txt"
    source.write_text("Revenue 123,456\nNet profit for the year 12,345\n", encoding="utf-8")

    requests: list[tuple[str, dict[str, str], object]] = []

    def fake_urlopen(request, timeout):
        headers = {k.lower(): v for k, v in request.header_items()}
        body = request.data.decode("utf-8", errors="ignore") if isinstance(request.data, bytes) else request.data
        requests.append((request.full_url, headers, body))

        if request.full_url.endswith("/ingest/file"):
            assert headers["x-api-key"] == "secret"
            assert "multipart/form-data" in headers["content-type"]
            assert 'name="file"; filename="income_statement.txt"' in body
            return FakeHttpResponse(
                {
                    "trace_id": "ingest-123",
                    "extraction": {
                        "document_id": "doc-123",
                        "strategy_used": "fast_text",
                        "review_required": False,
                    },
                }
            )

        payload = json.loads(body)
        assert request.full_url.endswith("/query/structured")
        assert payload["document_id"] == "doc-123"
        if payload["query"] == "total revenue":
            return FakeHttpResponse(
                {
                    "document_id": "doc-123",
                    "query": payload["query"],
                    "rows": [
                        {
                            "document_id": "doc-123",
                            "metric": "total revenue",
                            "value": 123456.0,
                            "unit": "usd",
                            "page_number": 1,
                            "content_hash": "abcdef123456",
                            "source_text": "Total revenue 123,456",
                        }
                    ],
                    "audit": [],
                }
            )
        if payload["query"] == "net income":
            return FakeHttpResponse({"document_id": "doc-123", "query": payload["query"], "rows": [], "audit": []})
        if payload["query"] == "net profit":
            return FakeHttpResponse(
                {
                    "document_id": "doc-123",
                    "query": payload["query"],
                    "rows": [
                        {
                            "document_id": "doc-123",
                            "metric": "net profit for the year",
                            "value": 12345.0,
                            "unit": "usd",
                            "page_number": 2,
                            "content_hash": "123456abcdef",
                            "source_text": "Net profit for the year 12,345",
                        }
                    ],
                    "audit": [],
                }
            )
        return FakeHttpResponse({"document_id": "doc-123", "query": payload["query"], "rows": [], "audit": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    facts = await client.extract_financial_facts(
        file_path=str(source),
        document_kind="income_statement",
        application_id="APP-123",
    )

    assert facts["total_revenue"] == 123456.0
    assert facts["net_income"] == 12345.0
    assert facts["field_confidence"]["total_revenue"] == 0.85
    assert facts["page_references"]["net_income"] == "page:2"
    assert any("rataz-Wordz document_id=doc-123" in note for note in facts["extraction_notes"])
    assert any("metric 'net profit for the year'" in note for note in facts["extraction_notes"])
    assert requests[0][0] == "http://127.0.0.1:8000/ingest/file"


@pytest.mark.asyncio
async def test_extraction_api_client_raises_if_ingest_response_has_no_document_id(monkeypatch, tmp_path):
    client = DocumentExtractionApiClient(base_url="http://127.0.0.1:8000")
    source = tmp_path / "balance_sheet.pdf"
    source.write_bytes(b"%PDF-1.4 mock")

    def fake_urlopen(request, timeout):
        return FakeHttpResponse({"trace_id": "ingest-123", "extraction": {}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="document_id"):
        await client.extract_financial_facts(
            file_path=str(source),
            document_kind="balance_sheet",
            application_id="APP-456",
        )
