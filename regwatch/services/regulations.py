"""Regulation catalog queries exposed to the UI layer."""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from regwatch.db.models import (
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationLifecycleLink,
)


@dataclass
class RegulationFilter:
    authorization_type: str | None = None
    is_ict: bool | None = None
    lifecycle_stages: list[str] | None = None
    search: str | None = None


@dataclass
class RegulationDTO:
    regulation_id: int
    reference_number: str
    title: str
    type: str
    issuing_authority: str
    lifecycle_stage: str
    is_ict: bool
    url: str
    transposition_deadline: date | None
    application_date: date | None
    needs_review: bool
    dora_pillar: str | None
    created_at: datetime
    publication_date: date | None = None


@dataclass
class AmendmentSummary:
    """The amendments rolled up under one listed regulation."""

    count: int
    # Newest publication date among the regulation and its amendments.
    last_change: date | None
    # The amendment that set last_change; None when it is the regulation's own.
    last_change_reference: str | None
    # last_change falls within the recent-changes window.
    is_recent: bool = False


def recent_changes_since(today: date) -> date:
    """Start of the "recent changes" window highlighted in the UI: 3 months."""
    year, month = (today.year, today.month - 3) if today.month > 3 else (
        today.year - 1, today.month + 9
    )
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def applies_to(authorization_type: str) -> ColumnElement[bool]:
    """Filter: the regulation applies to ``authorization_type``.

    Untagged regulations (GDPR, NIS2, ...) apply to every entity type.
    """
    tagged = select(RegulationApplicability.regulation_id)
    matching = tagged.where(
        RegulationApplicability.authorization_type.in_([authorization_type, "BOTH"])
    )
    return or_(
        Regulation.regulation_id.in_(matching),
        Regulation.regulation_id.not_in(tagged),
    )


class RegulationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, flt: RegulationFilter) -> list[RegulationDTO]:
        query = self._session.query(Regulation)

        if flt.authorization_type:
            query = query.filter(applies_to(flt.authorization_type))
        if flt.is_ict is not None:
            query = query.filter(Regulation.is_ict == flt.is_ict)
        if flt.lifecycle_stages:
            query = query.filter(
                Regulation.lifecycle_stage.in_(
                    [LifecycleStage(s) for s in flt.lifecycle_stages]
                )
            )
        if flt.search:
            like = f"%{flt.search}%"
            query = query.filter(
                or_(
                    Regulation.reference_number.ilike(like),
                    Regulation.title.ilike(like),
                )
            )

        rows = query.order_by(Regulation.reference_number).all()
        return [_to_dto(r) for r in rows]

    def get_by_reference(self, reference: str) -> RegulationDTO | None:
        reg = (
            self._session.query(Regulation)
            .filter(Regulation.reference_number == reference)
            .one_or_none()
        )
        return _to_dto(reg) if reg is not None else None


def _to_dto(r: Regulation) -> RegulationDTO:
    return RegulationDTO(
        regulation_id=r.regulation_id,
        reference_number=r.reference_number,
        title=r.title,
        type=r.type.value,
        issuing_authority=r.issuing_authority,
        lifecycle_stage=r.lifecycle_stage.value,
        is_ict=r.is_ict,
        url=r.url,
        transposition_deadline=r.transposition_deadline,
        application_date=r.application_date,
        needs_review=r.needs_review,
        dora_pillar=r.dora_pillar.value if r.dora_pillar else None,
        created_at=r.created_at,
        publication_date=r.publication_date,
    )


class AmendmentIndex:
    """Rolls amendments up under the regulation they amend, for list pages.

    Built once per request from ``build_amendment_indexes``: amendments are
    folded out of a listing into a "+N amendments" badge on their top-level
    regulation, and each listed regulation gets its last change date.
    """

    def __init__(self, session: Session) -> None:
        self._effective_parent_id, self._children = build_amendment_indexes(session)
        self._published: dict[int, tuple[str, date | None]] = {
            rid: (ref, published)
            for rid, ref, published in session.execute(
                select(
                    Regulation.regulation_id,
                    Regulation.reference_number,
                    Regulation.publication_date,
                )
            ).all()
        }

    def fold(self, regs: list[RegulationDTO]) -> list[RegulationDTO]:
        """Drop amendments whose top-level regulation is also in ``regs``.

        An amendment whose parent is filtered out of the listing (e.g. an
        ICT amendment of a non-ICT circular) stays, so nothing disappears.
        """
        listed = {r.regulation_id for r in regs}
        kept = []
        for r in regs:
            parent = self._effective_parent_id.get(r.regulation_id, r.regulation_id)
            if parent == r.regulation_id or parent not in listed:
                kept.append(r)
        return kept

    def summaries(
        self, regs: list[RegulationDTO], *, recent_since: date
    ) -> dict[int, AmendmentSummary]:
        result: dict[int, AmendmentSummary] = {}
        for r in regs:
            child_ids = self._children.get(r.regulation_id, [])
            dated = [
                (published, ref)
                for ref, published in (self._published[c] for c in child_ids)
                if published is not None
            ]
            newest = max(dated, default=None)
            last_change: date | None = r.publication_date
            by: str | None = None
            if newest is not None and (
                last_change is None or newest[0] >= last_change
            ):
                last_change, by = newest
            result[r.regulation_id] = AmendmentSummary(
                count=len(child_ids),
                last_change=last_change,
                last_change_reference=by,
                is_recent=last_change is not None and last_change >= recent_since,
            )
        return result


def build_amendment_indexes(
    session: Session,
) -> tuple[dict[int, int], dict[int, list[int]]]:
    """Build two maps for the current regulation catalog.

    Returns:
        (effective_parent_id, children_by_parent_id) where:
        - effective_parent_id[reg_id] = the top-level regulation_id this
          reg rolls up to. If reg is itself top-level, the value equals reg_id.
        - children_by_parent_id[parent_id] = list of non-top-level
          regulation_ids whose effective parent is this parent (parent excluded).

    Semantics:
    - A regulation with NO outgoing AMENDS edge to a non-REPEALED target
      is top-level.
    - Otherwise, walk outgoing AMENDS edges (pick any one if multiple) to
      non-REPEALED targets until we reach a top-level regulation.
    - Cycles are broken by a visited-set guard (defensive; shouldn't happen
      in practice but we don't trust data blindly).
    """
    # Load all regulations + their lifecycle stages
    regs: dict[int, Regulation] = {
        r.regulation_id: r
        for r in session.scalars(select(Regulation)).all()
    }
    # Load AMENDS edges: for each reg, list of target_ids where target is non-REPEALED.
    outgoing: dict[int, list[int]] = defaultdict(list)
    links = session.scalars(
        select(RegulationLifecycleLink).where(
            RegulationLifecycleLink.relation == "AMENDS"
        )
    ).all()
    for link in links:
        target = regs.get(link.to_regulation_id)
        if target is None or target.lifecycle_stage == LifecycleStage.REPEALED:
            continue
        outgoing[link.from_regulation_id].append(link.to_regulation_id)

    # Walk each reg to its top-level
    effective_parent_id: dict[int, int] = {}
    for rid in regs:
        current = rid
        visited: set[int] = set()
        while True:
            if current in visited:
                # cycle — declare current as top-level to stop
                break
            visited.add(current)
            nxt = outgoing.get(current)
            if not nxt:
                break
            current = nxt[0]  # pick first target (multi-parent not modelled yet)
        effective_parent_id[rid] = current

    # Invert to get children
    children_by_parent: dict[int, list[int]] = defaultdict(list)
    for child_id, parent_id in effective_parent_id.items():
        if child_id != parent_id:
            children_by_parent[parent_id].append(child_id)

    return effective_parent_id, dict(children_by_parent)
