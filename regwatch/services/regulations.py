"""Regulation catalog queries exposed to the UI layer."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from regwatch.db.models import (
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationLifecycleLink,
)


@dataclass
class RegulationFilter:
    authorization_type: Literal["AIFM", "CHAPTER15_MANCO"] | None = None
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


class RegulationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, flt: RegulationFilter) -> list[RegulationDTO]:
        query = self._session.query(Regulation)

        if flt.authorization_type:
            query = query.join(RegulationApplicability).filter(
                or_(
                    RegulationApplicability.authorization_type
                    == flt.authorization_type,
                    RegulationApplicability.authorization_type == "BOTH",
                )
            )
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
    )


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
