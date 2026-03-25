"""
ledger/agents/document_processing_agent.py
=========================================
DocumentProcessingAgent implementation.
"""
from __future__ import annotations
import time, json
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END

from ledger.agents.base_agent import BaseApexAgent
from ledger.domain.aggregates.loan_application import ApplicationState, LoanApplicationAggregate
from ledger.schema.events import (
    DocumentAdded,
    DocumentFormatValidated, DocumentFormatRejected,
    PackageCreated,
    ExtractionStarted, ExtractionCompleted, ExtractionFailed,
    QualityAssessmentCompleted, PackageReadyForAnalysis,
    CreditAnalysisRequested, DocumentFormat, FinancialFacts, DocumentType,
)


class DocProcState(TypedDict):
    application_id: str
    session_id: str
    document_ids: list[str] | None
    document_paths: list[str] | None
    extraction_results: list[dict] | None  # one per document
    quality_assessment: dict | None
    errors: list[str]
    output_events: list[dict]
    next_agent: str | None
    documents: list[dict] | None


class DocumentProcessingAgent(BaseApexAgent):
    """
    Wraps the Document Intelligence pipeline.

    LangGraph nodes:
        validate_inputs → validate_document_formats → extract_income_statement →
        extract_balance_sheet → assess_quality → write_output

    Output events:
        docpkg-{id}:  PackageCreated, DocumentAdded (x per doc), DocumentFormatValidated (x per doc),
                      ExtractionStarted (x per doc), ExtractionCompleted (x per doc), QualityAssessmentCompleted,
                      PackageReadyForAnalysis
        loan-{id}:    CreditAnalysisRequested
    """

    def __init__(self, *args, extraction_client: Any | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.extraction_client = extraction_client

    def _detect_format(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower().lstrip(".")
        return ext or "unknown"

    def _page_count(self, file_path: str) -> int:
        try:
            import PyPDF2
            with open(file_path, "rb") as f:
                return len(PyPDF2.PdfReader(f).pages)
        except Exception:
            return 1

    def _normalize_facts(self, facts: dict | None, required_fields: list[str]) -> dict:
        facts = dict(facts or {})
        field_conf = dict(facts.get("field_confidence", {}))
        notes = list(facts.get("extraction_notes", []))
        for field in required_fields:
            val = facts.get(field)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                facts[field] = None
                field_conf.setdefault(field, 0.0)
                notes.append(f"Missing field: {field}")
        facts["field_confidence"] = field_conf
        facts["extraction_notes"] = notes
        return facts

    @staticmethod
    def _coerce_document_type(raw: Any) -> DocumentType | None:
        try:
            return raw if isinstance(raw, DocumentType) else DocumentType(raw)
        except Exception:
            return None

    async def _extract_document_facts(self, file_path: str, document_kind: str) -> dict:
        """
        Adapter seam for the external extraction project.
        If an extraction client is injected, prefer that. Otherwise fall back to the local pipeline.
        """
        if self.extraction_client is not None:
            extractor = getattr(self.extraction_client, "extract_financial_facts", None)
            if extractor is None:
                raise AttributeError("extraction_client must expose extract_financial_facts(...)")
            result = extractor(file_path=file_path, document_kind=document_kind, application_id=self.application_id)
            if hasattr(result, "__await__"):
                return await result
            return result

        from document_refinery.pipeline import extract_financial_facts
        return await extract_financial_facts(file_path, document_kind)

    def _build_quality_fallback(self, combined: dict, critical_missing: list[str], anomalies: list[str]) -> dict:
        total_assets = self._to_float(combined.get("total_assets"))
        total_liabilities = self._to_float(combined.get("total_liabilities"))
        total_equity = self._to_float(combined.get("total_equity"))
        if None not in (total_assets, total_liabilities, total_equity):
            diff = abs(total_assets - total_liabilities - total_equity)
            if diff > 1.0 and "Balance sheet does not balance (Assets != Liabilities + Equity)" not in anomalies:
                anomalies.append("Balance sheet does not balance (Assets != Liabilities + Equity)")

        is_coherent = not critical_missing and not anomalies
        overall_confidence = max(0.0, min(1.0, 0.92 - (0.07 * len(critical_missing)) - (0.12 if anomalies else 0.0)))
        return {
            "overall_confidence": overall_confidence,
            "is_coherent": is_coherent,
            "anomalies": anomalies,
            "critical_missing_fields": critical_missing,
            "reextraction_recommended": bool(critical_missing or anomalies),
            "auditor_notes": "; ".join(anomalies) if anomalies else "Fallback quality assessment completed.",
        }

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    def build_graph(self):
        g = StateGraph(DocProcState)
        g.add_node("validate_inputs",            self._node_validate_inputs)
        g.add_node("validate_document_formats",  self._node_validate_formats)
        g.add_node("extract_income_statement",   self._node_extract_is)
        g.add_node("extract_balance_sheet",      self._node_extract_bs)
        g.add_node("assess_quality",             self._node_assess_quality)
        g.add_node("write_output",               self._node_write_output)

        g.set_entry_point("validate_inputs")
        g.add_edge("validate_inputs",           "validate_document_formats")
        g.add_edge("validate_document_formats", "extract_income_statement")
        g.add_edge("extract_income_statement",  "extract_balance_sheet")
        g.add_edge("extract_balance_sheet",     "assess_quality")
        g.add_edge("assess_quality",            "write_output")
        g.add_edge("write_output",              END)
        return g.compile()

    def _initial_state(self, application_id: str) -> DocProcState:
        return DocProcState(
            application_id=application_id, session_id=self.session_id,
            document_ids=None, document_paths=None,
            extraction_results=None, quality_assessment=None,
            errors=[], output_events=[], next_agent=None,
            documents=None,
        )

    async def _node_validate_inputs(self, state):
        t = time.time()
        app_id = state["application_id"]
        app = await LoanApplicationAggregate.load(self.store, app_id)
        app.require_state(ApplicationState.DOCUMENTS_UPLOADED)
        events = await self.store.load_stream(f"loan-{app_id}")
        pkg_stream = f"docpkg-{app_id}"
        pkg_events = await self.store.load_stream(pkg_stream)

        uploads = [e for e in events if e.get("event_type") == "DocumentUploaded"]
        upload_requests = [e for e in events if e.get("event_type") == "DocumentUploadRequested"]
        docs: list[dict] = []
        present_types: set[DocumentType] = set()
        existing_added = {
            (e.get("payload", {}) or {}).get("document_id")
            for e in pkg_events
            if e.get("event_type") == "DocumentAdded"
        }
        has_package_created = any(e.get("event_type") == "PackageCreated" for e in pkg_events)
        required_docs_payload: list[Any] = []
        package_created = next((e.get("payload", {}) for e in reversed(pkg_events) if e.get("event_type") == "PackageCreated"), None)
        if package_created:
            required_docs_payload = list(package_created.get("required_documents") or [])
        elif upload_requests:
            required_docs_payload = list((upload_requests[-1].get("payload", {}) or {}).get("required_document_types") or [])

        for ev in uploads:
            p = ev.get("payload", {})
            doc_type = p.get("document_type")
            try:
                doc_type_enum = DocumentType(doc_type)
            except Exception:
                doc_type_enum = None
            if doc_type_enum:
                present_types.add(doc_type_enum)
            docs.append({
                "document_id": p.get("document_id"),
                "document_type": doc_type_enum,
                "document_format": p.get("document_format"),
                "file_path": p.get("file_path"),
                "file_hash": p.get("file_hash"),
            })

        required = {
            document_type
            for document_type in (self._coerce_document_type(item) for item in required_docs_payload)
            if document_type is not None
        }
        if not required:
            required = {
                DocumentType.APPLICATION_PROPOSAL,
                DocumentType.INCOME_STATEMENT,
                DocumentType.BALANCE_SHEET,
            }
        missing = [d.value for d in required if d not in present_types]

        ms = int((time.time() - t) * 1000)
        if missing:
            await self._record_input_failed(missing, [f"Missing required documents: {missing}"])
            raise ValueError(f"Missing required documents: {missing}")

        package_events: list[dict] = []
        if not has_package_created:
            package_events.append(
                PackageCreated(
                    package_id=app_id,
                    application_id=app_id,
                    required_documents=sorted(required, key=lambda item: item.value),
                    created_at=datetime.now(),
                ).to_store_dict()
            )
        for doc in docs:
            if doc["document_id"] in existing_added:
                continue
            try:
                fmt = doc["document_format"]
                doc_format = fmt if isinstance(fmt, DocumentFormat) else DocumentFormat(fmt)
            except Exception:
                suffix = Path(doc.get("file_path") or "").suffix.lower().lstrip(".")
                doc_format = DocumentFormat(suffix) if suffix in {item.value for item in DocumentFormat} else DocumentFormat.PDF
            package_events.append(
                DocumentAdded(
                    package_id=app_id,
                    document_id=doc["document_id"],
                    document_type=doc["document_type"],
                    document_format=doc_format,
                    file_hash=doc.get("file_hash") or "unknown",
                    added_at=datetime.now(),
                ).to_store_dict()
            )
        if package_events:
            await self._append_with_retry(pkg_stream, package_events)

        await self._record_input_validated(["application_id", "document_ids", "file_paths"], ms)
        await self._record_node_execution(
            "validate_inputs",
            ["application_id"],
            ["document_ids", "document_paths"],
            ms,
        )
        return {
            **state,
            "document_ids": [d["document_id"] for d in docs],
            "document_paths": [d["file_path"] for d in docs],
            "documents": docs,
        }

    async def _node_validate_formats(self, state):
        t = time.time()
        app_id = state["application_id"]
        pkg_stream = f"docpkg-{app_id}"
        docs = state.get("documents") or []
        valid_docs: list[dict] = []

        for doc in docs:
            doc_id = doc.get("document_id")
            doc_type = doc.get("document_type")
            path = doc.get("file_path")
            if not path or not Path(path).exists():
                reject = DocumentFormatRejected(
                    package_id=app_id,
                    document_id=doc_id or "unknown",
                    rejection_reason="file_not_found",
                    rejected_at=datetime.now(),
                ).to_store_dict()
                await self._append_with_retry(pkg_stream, [reject])
                continue

            detected = self._detect_format(path)
            if detected not in ("pdf", "xlsx", "csv"):
                reject = DocumentFormatRejected(
                    package_id=app_id,
                    document_id=doc_id or "unknown",
                    rejection_reason=f"unsupported_format:{detected}",
                    rejected_at=datetime.now(),
                ).to_store_dict()
                await self._append_with_retry(pkg_stream, [reject])
                continue

            page_count = self._page_count(path) if detected == "pdf" else 1
            validated = DocumentFormatValidated(
                package_id=app_id,
                document_id=doc_id,
                document_type=doc_type,
                page_count=page_count,
                detected_format=detected,
                validated_at=datetime.now(),
            ).to_store_dict()
            await self._append_with_retry(pkg_stream, [validated])
            doc["detected_format"] = detected
            valid_docs.append(doc)

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "validate_document_formats",
            ["document_paths"],
            ["validated_documents"],
            ms,
        )
        return {
            **state,
            "documents": valid_docs,
            "document_ids": [d["document_id"] for d in valid_docs],
            "document_paths": [d["file_path"] for d in valid_docs],
        }

    async def _node_extract_is(self, state):
        t = time.time()
        app_id = state["application_id"]
        pkg_stream = f"docpkg-{app_id}"
        docs = state.get("documents") or []
        doc = next((d for d in docs if d.get("document_type") == DocumentType.INCOME_STATEMENT), None)
        if not doc:
            ms = int((time.time() - t) * 1000)
            await self._record_node_execution(
                "extract_income_statement",
                ["document_paths"],
                ["extraction_results"],
                ms,
            )
            return state

        doc_id = doc.get("document_id")
        file_path = doc.get("file_path")
        start_event = ExtractionStarted(
            package_id=app_id,
            document_id=doc_id,
            document_type=DocumentType.INCOME_STATEMENT,
            pipeline_version="week3-v1.0",
            extraction_model="mineru-1.0",
            started_at=datetime.now(),
        ).to_store_dict()
        await self._append_with_retry(pkg_stream, [start_event])

        try:
            facts_raw = await self._extract_document_facts(file_path, "income_statement")
        except Exception as e:
            fail_event = ExtractionFailed(
                package_id=app_id,
                document_id=doc_id,
                error_type=type(e).__name__,
                error_message=str(e)[:500],
                partial_facts=None,
                failed_at=datetime.now(),
            ).to_store_dict()
            await self._append_with_retry(pkg_stream, [fail_event])
            await self._record_tool_call(
                "week3_extraction_pipeline",
                f"income_statement path={file_path}",
                f"failed: {type(e).__name__}",
                int((time.time() - t) * 1000),
            )
            await self._record_node_execution(
                "extract_income_statement",
                ["document_paths"],
                ["extraction_failed"],
                int((time.time() - t) * 1000),
            )
            raise

        required_fields = [
            "total_revenue", "gross_profit", "operating_expenses",
            "operating_income", "ebitda", "depreciation_amortization",
            "interest_expense", "income_before_tax", "tax_expense", "net_income",
        ]
        facts_norm = self._normalize_facts(facts_raw, required_fields)
        facts = FinancialFacts(**facts_norm)

        completed = ExtractionCompleted(
            package_id=app_id,
            document_id=doc_id,
            document_type=DocumentType.INCOME_STATEMENT,
            facts=facts,
            raw_text_length=Path(file_path).stat().st_size if file_path and Path(file_path).exists() else 0,
            tables_extracted=max(1, len(facts_norm)),
            processing_ms=int((time.time() - t) * 1000),
            completed_at=datetime.now(),
        ).to_store_dict()
        await self._append_with_retry(pkg_stream, [completed])

        await self._record_tool_call(
            "week3_extraction_pipeline",
            f"income_statement path={file_path}",
            f"extracted_fields={len(facts_norm)}",
            int((time.time() - t) * 1000),
        )
        await self._record_node_execution(
            "extract_income_statement",
            ["document_paths"],
            ["extraction_results"],
            int((time.time() - t) * 1000),
        )
        results = list(state.get("extraction_results") or [])
        results.append({"document_type": "income_statement", "facts": facts_norm, "document_id": doc_id})
        return {**state, "extraction_results": results}

    async def _node_extract_bs(self, state):
        t = time.time()
        app_id = state["application_id"]
        pkg_stream = f"docpkg-{app_id}"
        docs = state.get("documents") or []
        doc = next((d for d in docs if d.get("document_type") == DocumentType.BALANCE_SHEET), None)
        if not doc:
            ms = int((time.time() - t) * 1000)
            await self._record_node_execution(
                "extract_balance_sheet",
                ["document_paths"],
                ["extraction_results"],
                ms,
            )
            return state

        doc_id = doc.get("document_id")
        file_path = doc.get("file_path")
        start_event = ExtractionStarted(
            package_id=app_id,
            document_id=doc_id,
            document_type=DocumentType.BALANCE_SHEET,
            pipeline_version="week3-v1.0",
            extraction_model="mineru-1.0",
            started_at=datetime.now(),
        ).to_store_dict()
        await self._append_with_retry(pkg_stream, [start_event])

        try:
            facts_raw = await self._extract_document_facts(file_path, "balance_sheet")
        except Exception as e:
            fail_event = ExtractionFailed(
                package_id=app_id,
                document_id=doc_id,
                error_type=type(e).__name__,
                error_message=str(e)[:500],
                partial_facts=None,
                failed_at=datetime.now(),
            ).to_store_dict()
            await self._append_with_retry(pkg_stream, [fail_event])
            await self._record_tool_call(
                "week3_extraction_pipeline",
                f"balance_sheet path={file_path}",
                f"failed: {type(e).__name__}",
                int((time.time() - t) * 1000),
            )
            await self._record_node_execution(
                "extract_balance_sheet",
                ["document_paths"],
                ["extraction_failed"],
                int((time.time() - t) * 1000),
            )
            raise

        required_fields = [
            "total_assets", "current_assets", "cash_and_equivalents",
            "accounts_receivable", "inventory", "total_liabilities",
            "current_liabilities", "long_term_debt", "total_equity",
        ]
        facts_norm = self._normalize_facts(facts_raw, required_fields)
        facts = FinancialFacts(**facts_norm)

        completed = ExtractionCompleted(
            package_id=app_id,
            document_id=doc_id,
            document_type=DocumentType.BALANCE_SHEET,
            facts=facts,
            raw_text_length=Path(file_path).stat().st_size if file_path and Path(file_path).exists() else 0,
            tables_extracted=max(1, len(facts_norm)),
            processing_ms=int((time.time() - t) * 1000),
            completed_at=datetime.now(),
        ).to_store_dict()
        await self._append_with_retry(pkg_stream, [completed])

        await self._record_tool_call(
            "week3_extraction_pipeline",
            f"balance_sheet path={file_path}",
            f"extracted_fields={len(facts_norm)}",
            int((time.time() - t) * 1000),
        )
        await self._record_node_execution(
            "extract_balance_sheet",
            ["document_paths"],
            ["extraction_results"],
            int((time.time() - t) * 1000),
        )
        results = list(state.get("extraction_results") or [])
        results.append({"document_type": "balance_sheet", "facts": facts_norm, "document_id": doc_id})
        return {**state, "extraction_results": results}

    async def _node_assess_quality(self, state):
        t = time.time()
        app_id = state["application_id"]
        pkg_stream = f"docpkg-{app_id}"
        results = state.get("extraction_results") or []

        combined: dict = {}
        field_conf: dict = {}
        notes: list[str] = []
        for r in results:
            facts = r.get("facts") or {}
            for k, v in facts.items():
                if k in ("field_confidence", "extraction_notes", "page_references"):
                    continue
                if k not in combined or combined[k] is None:
                    combined[k] = v
            for k, v in (facts.get("field_confidence") or {}).items():
                if k not in field_conf or (v is not None and v > field_conf.get(k, 0.0)):
                    field_conf[k] = v
            notes.extend(facts.get("extraction_notes") or [])

        critical_fields = ["total_revenue", "net_income", "ebitda", "total_assets", "total_liabilities", "total_equity"]
        critical_missing = [f for f in critical_fields if combined.get(f) is None]
        anomalies: list[str] = []
        llm_result: dict
        tok_in = tok_out = 0
        llm_cost = 0.0
        quality_prompt = json.dumps(
            {
                "combined_facts": {k: str(v) if v is not None else None for k, v in combined.items()},
                "field_confidence": field_conf,
                "notes": notes,
                "critical_fields": critical_fields,
            },
            indent=2,
        )
        system = (
            "You are a financial document quality analyst. You receive structured data extracted "
            "from a company's financial statements. Check ONLY:\n"
            "Internal consistency (Gross Profit = Revenue - COGS, Assets = Liabilities + Equity).\n"
            "Implausible values (margins > 80%, negative equity without note).\n"
            "Critical missing fields (total_revenue, net_income, total_assets, total_liabilities).\n"
            "Return JSON: "
            '{"overall_confidence": float, "is_coherent": bool, "anomalies": [str], '
            '"critical_missing_fields": [str], "reextraction_recommended": bool, "auditor_notes": str}. '
            "DO NOT make credit or lending decisions."
        )
        try:
            content, tok_in, tok_out, llm_cost = await self._call_llm(system, quality_prompt, max_tokens=512)
            llm_result = self._parse_json(content)
        except Exception as exc:
            notes.append(f"LLM quality assessment fallback used: {type(exc).__name__}")
            llm_result = self._build_quality_fallback(combined, critical_missing, anomalies)

        overall_confidence = float(llm_result.get("overall_confidence", 0.0))
        is_coherent = bool(llm_result.get("is_coherent", False))
        anomalies = list(llm_result.get("anomalies") or [])
        critical_missing = list(llm_result.get("critical_missing_fields") or critical_missing)
        reextraction_recommended = bool(llm_result.get("reextraction_recommended", False))
        auditor_notes = str(llm_result.get("auditor_notes") or "Quality assessment completed.")

        assessment = QualityAssessmentCompleted(
            package_id=app_id,
            document_id="combined",
            overall_confidence=overall_confidence,
            is_coherent=is_coherent,
            anomalies=anomalies,
            critical_missing_fields=critical_missing,
            reextraction_recommended=reextraction_recommended,
            auditor_notes=auditor_notes,
            assessed_at=datetime.now(),
        ).to_store_dict()
        await self._append_with_retry(pkg_stream, [assessment])

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "assess_quality",
            ["extraction_results"],
            ["quality_assessment"],
            ms,
            tok_in if tok_in else None,
            tok_out if tok_out else None,
            llm_cost if llm_cost else None,
        )
        return {**state, "quality_assessment": assessment["payload"]}

    async def _node_write_output(self, state):
        t = time.time()
        app_id = state["application_id"]
        pkg_stream = f"docpkg-{app_id}"
        loan_stream = f"loan-{app_id}"

        quality = state.get("quality_assessment") or {}
        critical_missing = quality.get("critical_missing_fields") or []
        has_quality_flags = len(critical_missing) > 0
        results = state.get("extraction_results") or []

        pkg_ready = PackageReadyForAnalysis(
            package_id=app_id,
            application_id=app_id,
            documents_processed=len(results),
            has_quality_flags=has_quality_flags,
            quality_flag_count=len(critical_missing),
            ready_at=datetime.now(),
        ).to_store_dict()
        pkg_positions = await self._append_with_retry(pkg_stream, [pkg_ready])

        credit_req = CreditAnalysisRequested(
            application_id=app_id,
            requested_at=datetime.now(),
            requested_by=self.agent_id,
        ).to_store_dict()
        loan_positions = await self._append_with_retry(loan_stream, [credit_req])

        events_written = [
            {"stream_id": pkg_stream, "event_type": "PackageReadyForAnalysis",
             "stream_position": pkg_positions[0] if pkg_positions else -1},
            {"stream_id": loan_stream, "event_type": "CreditAnalysisRequested",
             "stream_position": loan_positions[0] if loan_positions else -1},
        ]
        await self._record_output_written(
            events_written,
            f"Package ready. Quality flags: {len(critical_missing)}. Credit analysis requested.",
        )
        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "write_output",
            ["quality_assessment"],
            ["events_written"],
            ms,
        )
        return {**state, "output_events": events_written, "next_agent": "credit_analysis"}
