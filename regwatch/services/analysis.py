"""Service DTOs for AnalysisRun / DocumentAnalysis listings and detail pages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from regwatch.db.models import AnalysisRun, DocumentAnalysis, Regulation


@dataclass
class DocumentAnalysisDTO:
    analysis_id: int
    run_id: int
    version_id: int
    regulation_id: int | None
    status: str
    error_detail: str | None
    was_truncated: bool
    main_points: str | None
    scope_description: str | None
    applicable_entity_types: list[str] | None
    is_ict: bool | None
    ict_reasoning: str | None
    is_relevant_to_managed_entities: bool | None
    relevance_reasoning: str | None
    implementation_deadline: date | None
    deadline_description: str | None
    document_relationship: str | None
    relationship_target: str | None
    keywords: list[str] | None
    custom_fields: dict[str, Any]
    coercion_errors: dict[str, str] | None
    created_at: datetime
    raw_llm_output: str | None
    reference_number: str | None = None


@dataclass
class AnalysisRunDTO:
    run_id: int
    status: str
    queued_version_ids: list[int]
    started_at: datetime | None
    finished_at: datetime | None
    llm_model: str
    triggered_by: str
    error_summary: str | None
    analyses: list[DocumentAnalysisDTO]


class AnalysisService:
    def __init__(self, session: Session) -> None:
        self._s = session

    def latest_for_regulation(self, regulation_id: int) -> DocumentAnalysisDTO | None:
        row = (
            self._s.query(DocumentAnalysis)
            .filter_by(regulation_id=regulation_id)
            .order_by(desc(DocumentAnalysis.created_at), desc(DocumentAnalysis.analysis_id))
            .first()
        )
        return self._to_analysis_dto(row) if row else None

    def analyses_for_version(self, version_id: int) -> list[DocumentAnalysisDTO]:
        rows = (
            self._s.query(DocumentAnalysis)
            .filter_by(version_id=version_id)
            .order_by(desc(DocumentAnalysis.created_at), desc(DocumentAnalysis.analysis_id))
            .all()
        )
        return [self._to_analysis_dto(r) for r in rows]

    def get_run(self, run_id: int) -> AnalysisRunDTO | None:
        run = self._s.get(AnalysisRun, run_id)
        if run is None:
            return None
        return AnalysisRunDTO(
            run_id=run.run_id,
            status=run.status.value,
            queued_version_ids=list(run.queued_version_ids or []),
            started_at=run.started_at,
            finished_at=run.finished_at,
            llm_model=run.llm_model,
            triggered_by=run.triggered_by,
            error_summary=run.error_summary,
            analyses=[self._to_analysis_dto(a) for a in run.analyses],
        )

    def _to_analysis_dto(self, row: DocumentAnalysis) -> DocumentAnalysisDTO:
        ref_number: str | None = None
        if row.regulation_id is not None:
            reg = self._s.get(Regulation, row.regulation_id)
            if reg is not None:
                ref_number = reg.reference_number
        return DocumentAnalysisDTO(
            analysis_id=row.analysis_id,
            run_id=row.run_id,
            version_id=row.version_id,
            regulation_id=row.regulation_id,
            status=row.status.value,
            error_detail=row.error_detail,
            was_truncated=row.was_truncated,
            main_points=row.main_points,
            scope_description=row.scope_description,
            applicable_entity_types=row.applicable_entity_types,
            is_ict=row.is_ict,
            ict_reasoning=row.ict_reasoning,
            is_relevant_to_managed_entities=row.is_relevant_to_managed_entities,
            relevance_reasoning=row.relevance_reasoning,
            implementation_deadline=row.implementation_deadline,
            deadline_description=row.deadline_description,
            document_relationship=row.document_relationship,
            relationship_target=row.relationship_target,
            keywords=row.keywords,
            custom_fields=row.custom_fields or {},
            coercion_errors=row.coercion_errors,
            created_at=row.created_at,
            raw_llm_output=row.raw_llm_output,
            reference_number=ref_number,
        )
