"""Regulation catalog queries exposed to the UI layer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from regwatch.db.models import (
    LifecycleStage,
    Regulation,
    RegulationApplicability,
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
