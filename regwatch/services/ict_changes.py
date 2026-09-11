"""Latest changes to ICT regulations, for the dashboard overview."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentAnalysis,
    DocumentAnalysisStatus,
    LifecycleStage,
    Regulation,
    RegulationLifecycleLink,
    RegulationType,
)
from regwatch.services.analysis import DocumentAnalysisDTO
from regwatch.services.regulations import applies_to, recent_changes_since

_NEW_KIND_BY_TYPE = {
    RegulationType.CSSF_CIRCULAR: "New circular",
    RegulationType.CSSF_CIRCULAR_ANNEX: "New annex",
    RegulationType.CSSF_REGULATION: "New CSSF regulation",
    RegulationType.LU_LAW: "New law",
    RegulationType.LU_GRAND_DUCAL_REGULATION: "New regulation",
    RegulationType.LU_MINISTERIAL_REGULATION: "New regulation",
    RegulationType.EU_REGULATION: "New EU regulation",
    RegulationType.EU_DIRECTIVE: "New EU directive",
}


@dataclass
class AmendedRegulationDTO:
    regulation_id: int
    reference_number: str
    title: str


@dataclass
class IctChangeDTO:
    regulation_id: int
    reference_number: str
    # The title without its leading "Circular CSSF 26/915".
    headline: str
    kind: str  # "Amendment", "New circular", "New law", ...
    publication_date: date
    days_ago: int
    is_recent: bool
    lifecycle_stage: str
    dora_pillar: str | None
    # From the latest successful Analyse run; None until one has run.
    summary: str | None
    amends: list[AmendedRegulationDTO] = field(default_factory=list)


class IctChangesService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def latest(
        self,
        *,
        today: date,
        authorization_type: str | None = None,
        min_items: int = 5,
    ) -> list[IctChangeDTO]:
        """ICT regulations and amendments of ICT regulations, newest first.

        Everything published in the last 3 months, topped up with older
        items to ``min_items`` so the overview is never empty.
        """
        amends = self._amended_by_regulation()
        ict_ids = set(
            self._session.scalars(
                select(Regulation.regulation_id).where(Regulation.is_ict.is_(True))
            )
        )

        query = (
            select(Regulation)
            .where(Regulation.publication_date.is_not(None))
            .where(Regulation.lifecycle_stage != LifecycleStage.REPEALED)
            .order_by(desc(Regulation.publication_date), desc(Regulation.reference_number))
        )
        if authorization_type:
            query = query.where(applies_to(authorization_type))

        since = recent_changes_since(today)
        picked: list[tuple[Regulation, date]] = []
        for reg in self._session.scalars(query):
            published = reg.publication_date
            if published is None:
                continue
            touches_ict = reg.is_ict or any(
                a.regulation_id in ict_ids for a in amends[reg.regulation_id]
            )
            if not touches_ict:
                continue
            if published < since and len(picked) >= min_items:
                break
            picked.append((reg, published))

        summaries = self._summaries([r.regulation_id for r, _ in picked])
        return [
            IctChangeDTO(
                regulation_id=r.regulation_id,
                reference_number=r.reference_number,
                headline=_headline(r.title, r.reference_number),
                kind=(
                    "Amendment" if amends[r.regulation_id]
                    else _NEW_KIND_BY_TYPE.get(r.type, "New publication")
                ),
                publication_date=published,
                days_ago=(today - published).days,
                is_recent=published >= since,
                lifecycle_stage=r.lifecycle_stage.value,
                dora_pillar=r.dora_pillar.value if r.dora_pillar else None,
                summary=summaries.get(r.regulation_id),
                amends=amends[r.regulation_id],
            )
            for r, published in picked
        ]

    def _amended_by_regulation(self) -> defaultdict[int, list[AmendedRegulationDTO]]:
        rows = self._session.execute(
            select(
                RegulationLifecycleLink.from_regulation_id,
                Regulation.regulation_id,
                Regulation.reference_number,
                Regulation.title,
            )
            .join(Regulation, Regulation.regulation_id == RegulationLifecycleLink.to_regulation_id)
            .where(RegulationLifecycleLink.relation == "AMENDS")
            .order_by(Regulation.reference_number)
        ).all()
        result: defaultdict[int, list[AmendedRegulationDTO]] = defaultdict(list)
        for from_id, to_id, ref, title in rows:
            result[from_id].append(AmendedRegulationDTO(to_id, ref, title))
        return result

    def _summaries(self, regulation_ids: list[int]) -> dict[int, str]:
        """Scope description (or main points) of each regulation's newest
        successful analysis."""
        rows = self._session.scalars(
            select(DocumentAnalysis)
            .where(DocumentAnalysis.regulation_id.in_(regulation_ids))
            .where(DocumentAnalysis.status == DocumentAnalysisStatus.SUCCESS)
            .order_by(desc(DocumentAnalysis.created_at), desc(DocumentAnalysis.analysis_id))
        )
        result: dict[int, str] = {}
        for a in rows:
            if a.regulation_id is None or a.regulation_id in result:
                continue
            text = (a.scope_description or "").strip() or _main_points_text(a.main_points)
            if text:
                result[a.regulation_id] = text
        return result


def _main_points_text(main_points: str | None) -> str:
    """main_points flattened to one line (the LLM sometimes returns a list)."""
    display = DocumentAnalysisDTO.format_main_points(main_points)
    points = (line.lstrip("•-* ").strip() for line in display.splitlines())
    return "; ".join(p for p in points if p)


def _headline(title: str, reference: str) -> str:
    """The title without its leading "Circular <reference>", capitalised."""
    rest = re.sub(
        rf"^\s*(?:circular\s+)?{re.escape(reference)}\s*[–:\-]?\s*",
        "", title, count=1, flags=re.IGNORECASE,
    ).strip()
    if not rest:
        return title
    return rest[0].upper() + rest[1:]
