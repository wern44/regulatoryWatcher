"""Idempotent seeder for the default entity types.

Inserts AIFM and CHAPTER15_MANCO with the legacy CSSF filter IDs and
detail-page label substrings preserved from
``regwatch.services.cssf_discovery.CSSF_ENTITY_LABEL_TO_AUTH`` and
``CssfDiscoveryConfig.entity_filter_ids``.

Runs at app startup. If the table already has any rows, it's a no-op —
the user is in charge from that point onward (via Settings → Entity Types).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from regwatch.db.models import EntityType

_DEFAULTS: list[dict[str, object]] = [
    {
        "slug": "AIFM",
        "label": "AIFM",
        "cssf_entity_filter_id": 502,
        "cssf_detail_labels": [
            "Alternative investment fund manager",
            "AIFM",
        ],
        "sort_order": 10,
    },
    {
        "slug": "CHAPTER15_MANCO",
        "label": "Chapter 15 ManCo",
        "cssf_entity_filter_id": 2001,
        "cssf_detail_labels": [
            "UCITS management company",
            "UCITS management companies",
            "Chapter 15 management company",
            "Chapter 15 management companies",
            "Management company",
        ],
        "sort_order": 20,
    },
]


def seed_default_entity_types(session: Session) -> int:
    """Insert the two legacy entity types if the table is empty.

    Returns the number of rows inserted.
    """
    has_any = session.scalar(select(EntityType.entity_type_id).limit(1)) is not None
    if has_any:
        return 0
    for spec in _DEFAULTS:
        session.add(EntityType(**spec))
    session.flush()
    return len(_DEFAULTS)
