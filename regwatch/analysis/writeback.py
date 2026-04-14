"""Apply canonical-field updates from a DocumentAnalysis to its parent Regulation."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentAnalysis,
    DocumentVersion,
    Regulation,
    RegulationOverride,
    RegulationType,
)

logger = logging.getLogger(__name__)

_ICT_OVERRIDE_ACTIONS = {"SET_ICT", "UNSET_ICT", "EXCLUDE"}


def apply_writeback(session: Session, analysis: DocumentAnalysis) -> None:
    """Update canonical fields on the parent Regulation from this analysis.

    Only runs when this analysis belongs to the regulation's CURRENT version.
    For is_ict: DEFERS to RegulationOverride — if any SET_ICT/UNSET_ICT/EXCLUDE
    override exists, writeback does not touch is_ict. Applying the positive
    action of a SET_ICT/UNSET_ICT override is the responsibility of
    DiscoveryService.classify_catalog, not writeback.
    """
    current_version_id = session.scalar(
        select(DocumentVersion.version_id)
        .where(DocumentVersion.regulation_id == analysis.regulation_id)
        .where(DocumentVersion.is_current == True)  # noqa: E712
    )
    if current_version_id != analysis.version_id:
        return  # analysis of a non-current version never mutates the regulation

    reg = session.get(Regulation, analysis.regulation_id)
    if reg is None:
        return

    overrides = {
        r.action
        for r in session.query(RegulationOverride)
        .filter(RegulationOverride.reference_number == reg.reference_number)
        .all()
    }

    if analysis.is_ict is not None and not overrides & _ICT_OVERRIDE_ACTIONS:
        reg.is_ict = analysis.is_ict

    if analysis.applicable_entity_types is not None:
        reg.applicable_entity_types = analysis.applicable_entity_types

    if analysis.implementation_deadline is not None:
        _transposition_relationships = {None, "NEW", "REPLACES", "AMENDS"}
        if (
            _is_eu_directive(reg)
            and analysis.document_relationship in _transposition_relationships
        ):
            reg.transposition_deadline = analysis.implementation_deadline
        else:
            reg.application_date = analysis.implementation_deadline

    if analysis.document_relationship == "REPLACES" and analysis.relationship_target:
        old_reg = _resolve_reference(session, analysis.relationship_target)
        if old_reg is not None and old_reg.regulation_id != reg.regulation_id:
            old_reg.replaced_by_id = reg.regulation_id
        else:
            logger.info(
                "Could not resolve replaced reference '%s' for regulation '%s'",
                analysis.relationship_target, reg.reference_number,
            )

    session.flush()


def _is_eu_directive(reg: Regulation) -> bool:
    if reg.type is RegulationType.EU_DIRECTIVE:
        return True
    celex = reg.celex_id or ""
    # CELEX sector/type: e.g. 32022L2556 — the single letter after the year
    # marks the document type. 'L' = directive.
    return len(celex) >= 6 and celex[5] == "L"


def _resolve_reference(session: Session, ref: str) -> Regulation | None:
    ref = ref.strip()
    return session.scalar(
        select(Regulation).where(
            (Regulation.reference_number == ref) | (Regulation.celex_id == ref)
        )
    )
