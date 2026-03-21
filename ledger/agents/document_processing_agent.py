"""
ledger/agents/document_processing_agent.py
=========================================
DocumentProcessingAgent implementation.
"""
from __future__ import annotations
import time, json
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from langgraph.graph import StateGraph, END

from ledger.agents.base_agent import BaseApexAgent
from ledger.schema.events import (
    DocumentFormatValidated, DocumentFormatRejected,
    ExtractionStarted, ExtractionCompleted, ExtractionFailed,
    QualityAssessmentCompleted, PackageReadyForAnalysis,
    CreditAnalysisRequested, FinancialFacts, DocumentType,
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
        docpkg-{id}:  DocumentFormatValidated (x per doc), ExtractionStarted (x per doc),
                      ExtractionCompleted (x per doc), QualityAssessmentCompleted,
                      PackageReadyForAnalysis
        loan-{id}:    CreditAnalysisRequested
    """

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
        stream_id = f"loan-{app_id}"
        events = await self.store.load_stream(stream_id)

        uploads = [e for e in events if e.get("event_type") == "DocumentUploaded"]
        docs: list[dict] = []
        present_types: set[DocumentType] = set()

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
            })

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
            raise ValueError("Income statement document not found")

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
            from document_refinery.pipeline import extract_financial_facts
            facts_raw = await extract_financial_facts(file_path, "income_statement")
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
            raise ValueError("Balance sheet document not found")

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
            from document_refinery.pipeline import extract_financial_facts
            facts_raw = await extract_financial_facts(file_path, "balance_sheet")
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

        def _num(val):
            try:
                return float(val)
            except Exception:
                return None

        total_assets = _num(combined.get("total_assets"))
        total_liabilities = _num(combined.get("total_liabilities"))
        total_equity = _num(combined.get("total_equity"))
        balance_ok = None
        anomalies: list[str] = []
        if total_assets is not None and total_liabilities is not None and total_equity is not None:
            diff = abs(total_assets - total_liabilities - total_equity)
            balance_ok = diff <= 1.0
            if not balance_ok:
                anomalies.append("Balance sheet does not balance (Assets != Liabilities + Equity)")

        is_coherent = (balance_ok is not False) and len(critical_missing) == 0
        overall_confidence = 0.9
        if critical_missing:
            overall_confidence -= 0.05 * len(critical_missing)
        if balance_ok is False:
            overall_confidence -= 0.15
        overall_confidence = max(0.0, min(1.0, overall_confidence))

        assessment = QualityAssessmentCompleted(
            package_id=app_id,
            document_id="combined",
            overall_confidence=overall_confidence,
            is_coherent=is_coherent,
            anomalies=anomalies,
            critical_missing_fields=critical_missing,
            reextraction_recommended=bool(critical_missing) or balance_ok is False,
            auditor_notes="; ".join(anomalies + [f"Missing: {', '.join(critical_missing)}" if critical_missing else "No critical missing fields"]),
            assessed_at=datetime.now(),
        ).to_store_dict()
        await self._append_with_retry(pkg_stream, [assessment])

        ms = int((time.time() - t) * 1000)
        await self._record_node_execution(
            "assess_quality",
            ["extraction_results"],
            ["quality_assessment"],
            ms,
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
