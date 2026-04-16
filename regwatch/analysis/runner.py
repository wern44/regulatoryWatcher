"""Run analysis over a list of DocumentVersion ids: LLM call, persist, write-back."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from regwatch.analysis.extractor import extract
from regwatch.analysis.writeback import apply_writeback
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    ExtractionField,
    Regulation,
)
from regwatch.llm.client import LLMClient

logger = logging.getLogger(__name__)


class AnalysisRunner:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        llm: LLMClient,
        max_document_tokens: int,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self._sf = session_factory
        self._llm = llm
        self._max_tokens = max_document_tokens
        self._on_progress = on_progress or (lambda *_: None)

    def queue_and_run(
        self,
        version_ids: list[int],
        *,
        triggered_by: str,
        llm_model: str,
        existing_run_id: int | None = None,
    ) -> int:
        """Either create an AnalysisRun or update an existing one, then iterate."""
        if existing_run_id is None:
            with self._sf() as s:
                run = AnalysisRun(
                    status=AnalysisRunStatus.RUNNING,
                    queued_version_ids=list(version_ids),
                    started_at=datetime.now(UTC),
                    llm_model=llm_model,
                    triggered_by=triggered_by,
                )
                s.add(run)
                s.commit()
                run_id = run.run_id
        else:
            run_id = existing_run_id
            with self._sf() as s:
                run = s.get(AnalysisRun, run_id)
                if run is None:
                    raise RuntimeError(f"No AnalysisRun {run_id}")
                run.status = AnalysisRunStatus.RUNNING
                if run.started_at is None:
                    run.started_at = datetime.now(UTC)
                if llm_model and run.llm_model != llm_model:
                    run.llm_model = llm_model
                s.commit()

        # Pre-load regulation labels so progress shows meaningful names.
        version_labels: dict[int, str] = {}
        with self._sf() as s:
            for vid in version_ids:
                v = s.get(DocumentVersion, vid)
                if v is not None:
                    reg = s.get(Regulation, v.regulation_id)
                    if reg is not None:
                        version_labels[vid] = (
                            f"{reg.reference_number} — {reg.title}"
                        )
                if vid not in version_labels:
                    version_labels[vid] = f"version {vid}"

        succeeded = 0
        failed = 0
        errors: list[str] = []
        for i, vid in enumerate(version_ids, start=1):
            self._on_progress(i, len(version_ids), version_labels[vid])
            try:
                status = self._analyse_one(run_id, vid)
            except Exception as e:  # noqa: BLE001 — defensive: never kill the run
                logger.exception("Unexpected error analysing version %s", vid)
                status = DocumentAnalysisStatus.FAILED
                errors.append(f"version {vid}: {e}")
            if status is DocumentAnalysisStatus.SUCCESS:
                succeeded += 1
            else:
                failed += 1

        with self._sf() as s:
            run = s.get(AnalysisRun, run_id)
            if succeeded == len(version_ids):
                run.status = AnalysisRunStatus.SUCCESS
            elif succeeded == 0:
                run.status = AnalysisRunStatus.FAILED
            else:
                run.status = AnalysisRunStatus.PARTIAL
            run.finished_at = datetime.now(UTC)
            if errors:
                run.error_summary = "\n".join(errors)
            s.commit()
        return run_id

    def _analyse_one(self, run_id: int, version_id: int) -> DocumentAnalysisStatus:
        # Phase 1: read document text and metadata (short DB transaction).
        with self._sf() as s:
            version = s.get(DocumentVersion, version_id)
            if version is None:
                self._save_failure(s, run_id, version_id, None, "Version not found")
                return DocumentAnalysisStatus.FAILED

            text = version.pdf_extracted_text or version.html_text or ""
            if not text.strip():
                self._save_failure(
                    s, run_id, version_id, version.regulation_id,
                    "Document has no extracted text; upload manually or re-fetch.",
                )
                return DocumentAnalysisStatus.FAILED

            reg = s.get(Regulation, version.regulation_id)
            meta = (
                f"{reg.reference_number} — {reg.title} — {reg.issuing_authority}"
                if reg else f"version {version_id}"
            )
            regulation_id = version.regulation_id

        # Phase 2: call the LLM (no DB session held — can take minutes).
        with self._sf() as s:
            result = extract(
                session=s, llm=self._llm,
                regulation_metadata=meta, document_text=text, max_tokens=self._max_tokens,
            )

        # Phase 3: write results (short DB transaction).
        with self._sf() as s:
            if result.status == "FAILED":
                self._save_failure(
                    s, run_id, version_id, regulation_id,
                    result.error or "extraction failed", raw=result.raw_output,
                    was_truncated=result.was_truncated,
                )
                return DocumentAnalysisStatus.FAILED

            a = DocumentAnalysis(
                run_id=run_id, version_id=version_id, regulation_id=regulation_id,
                status=DocumentAnalysisStatus.SUCCESS,
                raw_llm_output=result.raw_output, was_truncated=result.was_truncated,
            )
            self._assign_core_values(a, result.values)
            a.custom_fields = self._collect_custom_values(s, result.values)
            a.coercion_errors = result.coercion_errors or None
            s.add(a)
            s.flush()
            apply_writeback(s, a)
            s.commit()
            return DocumentAnalysisStatus.SUCCESS

    @staticmethod
    def _assign_core_values(a: DocumentAnalysis, values: dict[str, object]) -> None:
        core_cols = {
            "main_points", "scope_description", "applicable_entity_types",
            "is_ict", "ict_reasoning", "is_relevant_to_managed_entities",
            "relevance_reasoning", "implementation_deadline", "deadline_description",
            "document_relationship", "relationship_target", "keywords",
        }
        for col in core_cols:
            if col in values:
                setattr(a, col, values[col])

    @staticmethod
    def _collect_custom_values(s: Session, values: dict[str, object]) -> dict[str, object]:
        custom_names = {
            f.name for f in s.query(ExtractionField).filter(
                ExtractionField.is_core == False,  # noqa: E712
                ExtractionField.is_active == True,  # noqa: E712
            ).all()
        }
        return {k: v for k, v in values.items() if k in custom_names}

    @staticmethod
    def _save_failure(
        s: Session, run_id: int, version_id: int, regulation_id: int | None,
        error: str, *, raw: str = "", was_truncated: bool = False,
    ) -> None:
        # regulation_id=None is allowed for orphan-version failure rows (column is nullable).
        a = DocumentAnalysis(
            run_id=run_id, version_id=version_id,
            regulation_id=regulation_id,
            status=DocumentAnalysisStatus.FAILED,
            error_detail=error, raw_llm_output=raw, was_truncated=was_truncated,
        )
        s.add(a)
        s.commit()
