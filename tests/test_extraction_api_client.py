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
async def test_extraction_api_client_falls_back_to_text_blocks_when_structured_query_is_empty(monkeypatch, tmp_path):
    client = DocumentExtractionApiClient(base_url="http://127.0.0.1:8000")
    source = tmp_path / "income_statement.pdf"
    source.write_bytes(b"%PDF-1.4 mock")

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/ingest/file"):
            return FakeHttpResponse(
                {
                    "trace_id": "ingest-456",
                    "extraction": {
                        "document_id": "doc-456",
                        "strategy_used": "fast_text",
                        "review_required": False,
                        "extracted_document": {
                            "text_blocks": [
                                {
                                    "content": "Revenue",
                                    "bounding_box": {"x0": 10, "y0": 100, "x1": 40, "y1": 110},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "$6,376,032",
                                    "bounding_box": {"x0": 200, "y0": 100, "x1": 260, "y1": 110},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "Net",
                                    "bounding_box": {"x0": 10, "y0": 120, "x1": 30, "y1": 130},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "Income",
                                    "bounding_box": {"x0": 35, "y0": 120, "x1": 80, "y1": 130},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "$120,142",
                                    "bounding_box": {"x0": 200, "y0": 120, "x1": 250, "y1": 130},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                            ],
                            "tables": [],
                        },
                    },
                }
            )
        return FakeHttpResponse({"document_id": "doc-456", "query": "unused", "rows": [], "audit": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    facts = await client.extract_financial_facts(
        file_path=str(source),
        document_kind="income_statement",
        application_id="APP-456",
    )

    assert facts["total_revenue"] == 6376032.0
    assert facts["net_income"] == 120142.0
    assert facts["field_confidence"]["total_revenue"] == 0.65
    assert facts["page_references"]["net_income"] == "page:1"
    assert any("derived from text blocks" in note for note in facts["extraction_notes"])


@pytest.mark.asyncio
async def test_extraction_api_client_prefers_best_text_block_match(monkeypatch, tmp_path):
    client = DocumentExtractionApiClient(base_url="http://127.0.0.1:8000")
    source = tmp_path / "balance_sheet.pdf"
    source.write_bytes(b"%PDF-1.4 mock")

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/ingest/file"):
            return FakeHttpResponse(
                {
                    "trace_id": "ingest-789",
                    "extraction": {
                        "document_id": "doc-789",
                        "strategy_used": "plain_text",
                        "review_required": False,
                        "extracted_document": {
                            "text_blocks": [
                                {
                                    "content": "Total",
                                    "bounding_box": {"x0": 10, "y0": 100, "x1": 40, "y1": 110},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "Current",
                                    "bounding_box": {"x0": 45, "y0": 100, "x1": 90, "y1": 110},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "Assets",
                                    "bounding_box": {"x0": 95, "y0": 100, "x1": 140, "y1": 110},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "$5,350,573",
                                    "bounding_box": {"x0": 200, "y0": 100, "x1": 260, "y1": 110},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "Total",
                                    "bounding_box": {"x0": 10, "y0": 120, "x1": 40, "y1": 130},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "Assets",
                                    "bounding_box": {"x0": 45, "y0": 120, "x1": 90, "y1": 130},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                                {
                                    "content": "$14,965,437",
                                    "bounding_box": {"x0": 200, "y0": 120, "x1": 270, "y1": 130},
                                    "page_refs": [{"page_start": 1, "page_end": 1}],
                                },
                            ],
                            "tables": [],
                        },
                    },
                }
            )
        return FakeHttpResponse({"document_id": "doc-789", "query": "unused", "rows": [], "audit": []})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    facts = await client.extract_financial_facts(
        file_path=str(source),
        document_kind="balance_sheet",
        application_id="APP-789",
    )

    assert facts["total_assets"] == 14965437.0
    assert facts["current_assets"] == 5350573.0


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
