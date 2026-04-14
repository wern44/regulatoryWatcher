"""Seed the non-deletable core extraction fields."""
from __future__ import annotations

from sqlalchemy.orm import Session

from regwatch.db.models import ExtractionField, ExtractionFieldType

_CORE_FIELDS: list[dict[str, object]] = [
    {
        "name": "main_points",
        "label": "Main Points",
        "description": (
            "Summarize the document in 3-5 bullet points focusing on "
            "key obligations and scope."
        ),
        "data_type": ExtractionFieldType.LONG_TEXT,
        "canonical_field": None,
        "display_order": 10,
    },
    {
        "name": "scope_description",
        "label": "Scope",
        "description": (
            "Describe in one paragraph which activities, products, and "
            "processes are covered by this document."
        ),
        "data_type": ExtractionFieldType.LONG_TEXT,
        "canonical_field": None,
        "display_order": 20,
    },
    {
        "name": "applicable_entity_types",
        "label": "Applicable Entity Types",
        "description": (
            "List the CSSF / EU entity types this document applies to. "
            "Valid values: AIFM, CHAPTER15_MANCO, CREDIT_INSTITUTION, "
            "CASP, INVESTMENT_FIRM, INSURANCE, PENSION_FUND, ALL."
        ),
        "data_type": ExtractionFieldType.LIST_TEXT,
        "canonical_field": "applicable_entity_types",
        "display_order": 30,
    },
    {
        "name": "is_ict",
        "label": "ICT / DORA Related",
        "description": (
            "True if the document addresses ICT risk, cybersecurity, "
            "digital operational resilience, IT outsourcing, or similar "
            "technology-risk topics. False otherwise."
        ),
        "data_type": ExtractionFieldType.BOOL,
        "canonical_field": "is_ict",
        "display_order": 40,
    },
    {
        "name": "ict_reasoning",
        "label": "ICT Reasoning",
        "description": (
            "One sentence explaining why this document is or is not ICT-related."
        ),
        "data_type": ExtractionFieldType.TEXT,
        "canonical_field": None,
        "display_order": 50,
    },
    {
        "name": "is_relevant_to_managed_entities",
        "label": "Relevant to Our Entities",
        "description": (
            "True if this document is directly relevant to the entity types "
            "our tool manages (AIFM and CHAPTER15_MANCO). False otherwise."
        ),
        "data_type": ExtractionFieldType.BOOL,
        "canonical_field": None,
        "display_order": 60,
    },
    {
        "name": "relevance_reasoning",
        "label": "Relevance Reasoning",
        "description": (
            "One sentence explaining the relevance (or lack thereof) "
            "to our managed entities."
        ),
        "data_type": ExtractionFieldType.TEXT,
        "canonical_field": None,
        "display_order": 70,
    },
    {
        "name": "implementation_deadline",
        "label": "Implementation Deadline",
        "description": (
            "The latest date by which addressees must comply with this "
            "document. ISO-8601 date (YYYY-MM-DD) or null if not specified."
        ),
        "data_type": ExtractionFieldType.DATE,
        "canonical_field": "implementation_deadline",
        "display_order": 80,
    },
    {
        "name": "deadline_description",
        "label": "Deadline Detail",
        "description": (
            "Short text explaining the deadline (e.g. 'Enters into force "
            "6 months after publication')."
        ),
        "data_type": ExtractionFieldType.TEXT,
        "canonical_field": None,
        "display_order": 90,
    },
    {
        "name": "document_relationship",
        "label": "Relationship to Existing Documents",
        "description": (
            "Is this document NEW, REPLACES an existing document, AMENDS "
            "one, REPEALS one, or CLARIFIES one?"
        ),
        "data_type": ExtractionFieldType.ENUM,
        "enum_values": ["NEW", "REPLACES", "AMENDS", "REPEALS", "CLARIFIES"],
        "canonical_field": None,
        "display_order": 100,
    },
    {
        "name": "relationship_target",
        "label": "Related Document Reference",
        "description": (
            "If this document REPLACES, AMENDS, REPEALS or CLARIFIES another, "
            "its reference (e.g. 'CSSF 12/552'). Null otherwise."
        ),
        "data_type": ExtractionFieldType.TEXT,
        "canonical_field": None,
        "display_order": 110,
    },
    {
        "name": "keywords",
        "label": "Keywords",
        "description": (
            "List of 5-10 short keywords or key-phrases capturing the "
            "document's main topics."
        ),
        "data_type": ExtractionFieldType.LIST_TEXT,
        "canonical_field": None,
        "display_order": 120,
    },
]


def seed_core_fields(session: Session) -> int:
    """Insert any core fields that don't yet exist. Returns the number inserted.

    Also patches the document_relationship.enum_values if a legacy 'APPEALS'
    value is present (typo fix applied after initial deployment).
    """
    existing_by_name = {f.name: f for f in session.query(ExtractionField).all()}

    # One-time legacy patch: APPEALS -> REPEALS in document_relationship
    legacy = existing_by_name.get("document_relationship")
    if legacy is not None and legacy.enum_values and "APPEALS" in legacy.enum_values:
        legacy.enum_values = [
            "REPEALS" if v == "APPEALS" else v for v in legacy.enum_values
        ]
        # Also refresh the description from the canonical _CORE_FIELDS spec
        canonical = next(
            f for f in _CORE_FIELDS if f["name"] == "document_relationship"
        )
        legacy.description = canonical["description"]
        session.flush()

    inserted = 0
    for spec in _CORE_FIELDS:
        if spec["name"] in existing_by_name:
            continue
        row = ExtractionField(
            name=spec["name"],
            label=spec["label"],
            description=spec["description"],
            data_type=spec["data_type"],
            enum_values=spec.get("enum_values"),
            is_core=True,
            is_active=True,
            canonical_field=spec.get("canonical_field"),
            display_order=spec["display_order"],
        )
        session.add(row)
        inserted += 1
    session.flush()
    return inserted
