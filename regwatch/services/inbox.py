"""Inbox service: triage of pipeline update events."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, desc
from sqlalchemy.orm import Session

from regwatch.db.models import UpdateEvent

# Ordering key: CRITICAL (0) < MATERIAL (1) < INFORMATIONAL (2). Unknown -> 3.
_SEVERITY_ORDER = {
    "CRITICAL": 0,
    "MATERIAL": 1,
    "INFORMATIONAL": 2,
}


@dataclass
class UpdateEventDTO:
    event_id: int
    source: str
    source_url: str
    title: str
    published_at: datetime
    severity: str
    review_status: str
    is_ict: bool | None
    seen_at: datetime | None


class InboxService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_new(self) -> list[UpdateEventDTO]:
        severity_rank = case(
            _SEVERITY_ORDER,
            value=UpdateEvent.severity,
            else_=3,
        )
        rows = (
            self._session.query(UpdateEvent)
            .filter(UpdateEvent.review_status == "NEW")
            .order_by(severity_rank, desc(UpdateEvent.published_at))
            .all()
        )
        return [_to_dto(r) for r in rows]

    def list_by_severity(self, severity: str) -> list[UpdateEventDTO]:
        rows = (
            self._session.query(UpdateEvent)
            .filter(UpdateEvent.severity == severity)
            .filter(UpdateEvent.review_status == "NEW")
            .order_by(desc(UpdateEvent.published_at))
            .all()
        )
        return [_to_dto(r) for r in rows]

    def count_new(self) -> int:
        return (
            self._session.query(UpdateEvent)
            .filter(UpdateEvent.review_status == "NEW")
            .count()
        )

    def mark_seen(self, event_id: int) -> None:
        ev = self._session.get(UpdateEvent, event_id)
        if ev is None:
            raise ValueError(f"UpdateEvent {event_id} not found")
        ev.review_status = "SEEN"
        ev.seen_at = datetime.now(UTC)

    def archive(self, event_id: int) -> None:
        ev = self._session.get(UpdateEvent, event_id)
        if ev is None:
            raise ValueError(f"UpdateEvent {event_id} not found")
        ev.review_status = "ARCHIVED"


def _to_dto(ev: UpdateEvent) -> UpdateEventDTO:
    return UpdateEventDTO(
        event_id=ev.event_id,
        source=ev.source,
        source_url=ev.source_url,
        title=ev.title,
        published_at=ev.published_at,
        severity=ev.severity,
        review_status=ev.review_status,
        is_ict=ev.is_ict,
        seen_at=ev.seen_at,
    )
