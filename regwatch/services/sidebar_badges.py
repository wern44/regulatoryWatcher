"""Counts of items added since the user's last visit to each sidebar section."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from regwatch.db.models import LifecycleStage, Regulation, Setting, UpdateEvent

SECTION_KEYS: dict[str, str] = {
    "inbox": "last_visit_inbox",
    "catalog": "last_visit_catalog",
    "ict": "last_visit_ict",
    "drafts": "last_visit_drafts",
    "deadlines": "last_visit_deadlines",
}

_DRAFTY_LIFECYCLES = (
    LifecycleStage.CONSULTATION,
    LifecycleStage.PROPOSAL,
    LifecycleStage.DRAFT_BILL,
    LifecycleStage.ADOPTED_NOT_IN_FORCE,
)


@dataclass(frozen=True)
class SidebarBadges:
    inbox: int
    catalog: int
    ict: int
    drafts: int
    deadlines: int


class SidebarBadgeService:
    """Reads and writes per-section last-visit timestamps."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def counts(self) -> SidebarBadges:
        """Return the new-item counts for each section.

        A missing setting key for a section means "user has just visited":
        the count is 0 and no items qualify as new until the next visit.
        """
        return SidebarBadges(
            inbox=self._count_inbox(),
            catalog=self._count_catalog(),
            ict=self._count_ict(),
            drafts=self._count_drafts(),
            deadlines=self._count_deadlines(),
        )

    def mark_visited(self, section: str) -> None:
        """Upsert last_visit_<section> = now."""
        if section not in SECTION_KEYS:
            raise ValueError(f"unknown section: {section!r}")
        key = SECTION_KEYS[section]
        now = datetime.now(UTC)
        existing = self._session.get(Setting, key)
        if existing is None:
            self._session.add(Setting(key=key, value=now.isoformat(), updated_at=now))
        else:
            existing.value = now.isoformat()
            existing.updated_at = now

    def _last_visit(self, section: str) -> datetime | None:
        row = self._session.get(Setting, SECTION_KEYS[section])
        if row is None:
            return None
        ts = datetime.fromisoformat(row.value)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts

    def _count_inbox(self) -> int:
        cutoff = self._last_visit("inbox")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(UpdateEvent.event_id)).where(
                UpdateEvent.fetched_at > cutoff
            )
        )
        return int(n or 0)

    def _count_catalog(self) -> int:
        cutoff = self._last_visit("catalog")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(Regulation.regulation_id)).where(
                Regulation.created_at > cutoff
            )
        )
        return int(n or 0)

    def _count_ict(self) -> int:
        cutoff = self._last_visit("ict")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(Regulation.regulation_id)).where(
                Regulation.created_at > cutoff,
                Regulation.is_ict.is_(True),
            )
        )
        return int(n or 0)

    def _count_drafts(self) -> int:
        cutoff = self._last_visit("drafts")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(Regulation.regulation_id)).where(
                Regulation.created_at > cutoff,
                Regulation.lifecycle_stage.in_(_DRAFTY_LIFECYCLES),
            )
        )
        return int(n or 0)

    def _count_deadlines(self) -> int:
        cutoff = self._last_visit("deadlines")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(Regulation.regulation_id)).where(
                Regulation.created_at > cutoff,
                (
                    Regulation.transposition_deadline.is_not(None)
                    | Regulation.application_date.is_not(None)
                ),
            )
        )
        return int(n or 0)
