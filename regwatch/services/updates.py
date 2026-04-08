"""Update events and document-version diffs exposed to the UI."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentVersion,
    Regulation,
    UpdateEvent,
)
from regwatch.pipeline.diff import compute_diff


@dataclass
class LinkedRegulationDTO:
    regulation_id: int
    reference_number: str
    title: str
    match_method: str
    matched_snippet: str | None


@dataclass
class EventDetailDTO:
    event_id: int
    source: str
    source_url: str
    title: str
    published_at: datetime
    severity: str
    is_ict: bool | None
    review_status: str
    regulations: list[LinkedRegulationDTO]


@dataclass
class VersionDTO:
    version_id: int
    regulation_id: int
    version_number: int
    is_current: bool
    fetched_at: datetime
    source_url: str
    change_summary: str | None


@dataclass
class DiffDTO:
    regulation_id: int
    from_version: int
    to_version: int
    diff_text: str


class UpdateService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_event(self, event_id: int) -> EventDetailDTO | None:
        ev = self._session.get(UpdateEvent, event_id)
        if ev is None:
            return None

        linked: list[LinkedRegulationDTO] = []
        for link in ev.regulation_links:
            reg = self._session.get(Regulation, link.regulation_id)
            if reg is None:
                continue
            linked.append(
                LinkedRegulationDTO(
                    regulation_id=reg.regulation_id,
                    reference_number=reg.reference_number,
                    title=reg.title,
                    match_method=link.match_method,
                    matched_snippet=link.matched_snippet,
                )
            )
        return EventDetailDTO(
            event_id=ev.event_id,
            source=ev.source,
            source_url=ev.source_url,
            title=ev.title,
            published_at=ev.published_at,
            severity=ev.severity,
            is_ict=ev.is_ict,
            review_status=ev.review_status,
            regulations=linked,
        )

    def list_versions(self, regulation_id: int) -> list[VersionDTO]:
        rows = (
            self._session.query(DocumentVersion)
            .filter(DocumentVersion.regulation_id == regulation_id)
            .order_by(DocumentVersion.version_number)
            .all()
        )
        return [
            VersionDTO(
                version_id=v.version_id,
                regulation_id=v.regulation_id,
                version_number=v.version_number,
                is_current=v.is_current,
                fetched_at=v.fetched_at,
                source_url=v.source_url,
                change_summary=v.change_summary,
            )
            for v in rows
        ]

    def compare_versions(
        self, regulation_id: int, a: int, b: int
    ) -> DiffDTO | None:
        versions = (
            self._session.query(DocumentVersion)
            .filter(DocumentVersion.regulation_id == regulation_id)
            .filter(DocumentVersion.version_number.in_([a, b]))
            .all()
        )
        by_num = {v.version_number: v for v in versions}
        va = by_num.get(a)
        vb = by_num.get(b)
        if va is None or vb is None:
            return None

        text_a = va.pdf_extracted_text or va.html_text or ""
        text_b = vb.pdf_extracted_text or vb.html_text or ""
        diff = compute_diff(text_a, text_b) or ""
        return DiffDTO(
            regulation_id=regulation_id,
            from_version=a,
            to_version=b,
            diff_text=diff,
        )
