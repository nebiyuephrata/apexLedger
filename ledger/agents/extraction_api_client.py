"""
ledger/agents/extraction_api_client.py
======================================
HTTP client adapter for an external document extraction service.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import urllib.error
import urllib.request
from pathlib import Path


FACT_QUERY_MAP: dict[str, dict[str, tuple[str, ...]]] = {
    "income_statement": {
        "total_revenue": ("total revenue", "revenue"),
        "gross_profit": ("gross profit",),
        "operating_expenses": ("operating expenses", "total operating expenses"),
        "operating_income": ("operating income", "income from operations"),
        "ebitda": ("ebitda",),
        "depreciation_amortization": ("depreciation amortization", "depreciation and amortization"),
        "interest_expense": ("interest expense",),
        "income_before_tax": ("income before tax", "pretax income", "profit before tax"),
        "tax_expense": ("tax expense", "income tax expense"),
        "net_income": ("net income", "net profit", "profit for the year"),
    },
    "balance_sheet": {
        "total_assets": ("total assets",),
        "current_assets": ("current assets",),
        "cash_and_equivalents": ("cash and equivalents", "cash"),
        "accounts_receivable": ("accounts receivable", "receivables"),
        "inventory": ("inventory",),
        "total_liabilities": ("total liabilities",),
        "current_liabilities": ("current liabilities",),
        "long_term_debt": ("long term debt", "long-term debt"),
        "total_equity": ("total equity", "shareholders equity", "equity"),
    },
}

LINE_VALUE_RE = re.compile(r"\(?-?\$?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?")
TEXT_NUMBER_RE = re.compile(r"\(?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?")


class DocumentExtractionApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        endpoint: str = "/ingest/file",
        structured_query_endpoint: str = "/query/structured",
        timeout_seconds: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self.structured_query_endpoint = (
            structured_query_endpoint
            if structured_query_endpoint.startswith("/")
            else f"/{structured_query_endpoint}"
        )
        self.timeout_seconds = timeout_seconds

    async def extract_financial_facts(
        self,
        *,
        file_path: str,
        document_kind: str,
        application_id: str,
    ) -> dict:
        def _request() -> dict:
            ingest_result = self._post_multipart_file(file_path)
            extraction = ingest_result.get("extraction")
            if not isinstance(extraction, dict):
                raise RuntimeError("Extraction API response missing 'extraction' object")
            document_id = extraction.get("document_id")
            if not document_id:
                raise RuntimeError("Extraction API response missing document_id")
            return self._load_financial_facts(
                ingest_result=ingest_result,
                document_id=document_id,
                document_kind=document_kind,
                application_id=application_id,
                strategy_used=extraction.get("strategy_used"),
                review_required=extraction.get("review_required"),
            )

        return await asyncio.to_thread(_request)

    def _build_headers(self, content_type: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _open_json(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Extraction API error {exc.code}: {detail}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"Unexpected extraction API response shape: {type(raw)!r}")
        return raw

    def _post_multipart_file(self, file_path: str) -> dict:
        path = Path(file_path)
        filename = path.name
        boundary = "----ledger-doc-upload-boundary"
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        file_bytes = path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{self.endpoint}",
            data=body,
            headers=self._build_headers(f"multipart/form-data; boundary={boundary}"),
            method="POST",
        )
        return self._open_json(request)

    def _post_structured_query(self, *, document_id: str, query: str, limit: int = 3) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{self.structured_query_endpoint}",
            data=json.dumps({"document_id": document_id, "query": query, "limit": limit}).encode("utf-8"),
            headers=self._build_headers("application/json"),
            method="POST",
        )
        return self._open_json(request)

    def _load_financial_facts(
        self,
        *,
        ingest_result: dict,
        document_id: str,
        document_kind: str,
        application_id: str,
        strategy_used: str | None,
        review_required: bool | None,
    ) -> dict:
        field_map = FACT_QUERY_MAP.get(document_kind, {})
        facts: dict[str, object] = {
            "field_confidence": {},
            "page_references": {},
            "extraction_notes": [
                f"Extracted via rataz-Wordz document_id={document_id}",
                f"Application context: {application_id}",
            ],
        }
        if strategy_used:
            facts["extraction_notes"].append(f"Extraction strategy: {strategy_used}")
        if review_required:
            facts["extraction_notes"].append("Extractor flagged review_required=true")

        text_block_candidates = self._extract_candidates_from_text_blocks(ingest_result)
        for field_name, aliases in field_map.items():
            row = None
            matched_alias = None
            for alias in aliases:
                response = self._post_structured_query(document_id=document_id, query=alias, limit=3)
                rows = response.get("rows") or []
                if rows:
                    row = rows[0]
                    matched_alias = alias
                    break
            if isinstance(row, dict):
                value = row.get("value")
                if value is not None:
                    facts[field_name] = value
                    facts["field_confidence"][field_name] = 0.85
                    page_number = row.get("page_number")
                    if page_number:
                        facts["page_references"][field_name] = f"page:{page_number}"
                    metric = str(row.get("metric") or "")
                    if matched_alias and metric and metric != matched_alias:
                        facts["extraction_notes"].append(
                            f"Field '{field_name}' matched via alias '{matched_alias}' using metric '{metric}'."
                        )
                    source_text = row.get("source_text")
                    if source_text:
                        facts["extraction_notes"].append(f"{field_name} source: {source_text}")
                    continue

            fallback = self._match_text_block_candidate(text_block_candidates, aliases)
            if fallback is None:
                continue
            facts[field_name] = fallback["value"]
            facts["field_confidence"][field_name] = 0.65
            facts["page_references"][field_name] = f"page:{fallback['page_number']}"
            facts["extraction_notes"].append(
                f"Field '{field_name}' derived from text blocks using line '{fallback['source_text']}'."
            )
        return facts

    @staticmethod
    def _to_float(raw: str) -> float | None:
        cleaned = raw.strip().replace("$", "")
        negative = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.strip("()").replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            return None
        return -value if negative else value

    def _extract_candidates_from_text_blocks(self, ingest_result: dict) -> list[dict]:
        extraction = ingest_result.get("extraction") or {}
        extracted_document = extraction.get("extracted_document") or {}
        text_blocks = extracted_document.get("text_blocks") or []
        grouped: dict[tuple[int, int], list[tuple[float, str]]] = {}
        for block in text_blocks:
            if not isinstance(block, dict):
                continue
            content = str(block.get("content") or "").strip()
            bbox = block.get("bounding_box") or {}
            page_refs = block.get("page_refs") or []
            if not content or not isinstance(bbox, dict):
                continue
            page = 1
            if page_refs and isinstance(page_refs[0], dict):
                page = int(page_refs[0].get("page_start") or 1)
            try:
                x0 = float(bbox.get("x0", 0.0))
                y0 = float(bbox.get("y0", 0.0))
            except (TypeError, ValueError):
                continue
            line_key = (page, round(y0))
            grouped.setdefault(line_key, []).append((x0, content))

        candidates: list[dict] = []
        for (page, _line), tokens in grouped.items():
            ordered = [token for _x, token in sorted(tokens, key=lambda item: item[0])]
            line = " ".join(ordered)
            values = LINE_VALUE_RE.findall(line)
            if not values:
                continue
            raw_value = values[-1]
            value = self._to_float(raw_value)
            if value is None:
                continue
            metric = LINE_VALUE_RE.sub("", line).strip(" :.-").lower()
            metric = " ".join(metric.split())
            if len(metric) < 3:
                continue
            candidates.append(
                {
                    "metric": metric,
                    "value": value,
                    "page_number": page,
                    "source_text": line,
                }
            )
        return candidates

    @staticmethod
    def _match_text_block_candidate(candidates: list[dict], aliases: tuple[str, ...]) -> dict | None:
        lowered_aliases = [alias.lower() for alias in aliases]
        best: tuple[int, dict] | None = None
        for alias in lowered_aliases:
            alias_tokens = set(TEXT_NUMBER_RE.sub("", alias).split())
            for candidate in candidates:
                metric = str(candidate.get("metric") or "")
                metric_tokens = set(metric.split())
                score = None
                if metric == alias:
                    score = 1000
                elif metric_tokens == alias_tokens and alias_tokens:
                    score = 950
                elif alias in metric or metric in alias:
                    score = 700 - max(0, len(metric_tokens) - len(alias_tokens)) * 10
                elif alias_tokens and alias_tokens.issubset(metric_tokens):
                    score = 600 - max(0, len(metric_tokens) - len(alias_tokens)) * 10
                if score is None:
                    continue
                if metric.startswith("total "):
                    score += 25
                if "other " in metric:
                    score -= 25
                if best is None or score > best[0]:
                    best = (score, candidate)
        return best[1] if best else None
