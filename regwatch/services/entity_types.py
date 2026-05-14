"""CRUD service for the entity_type registry."""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from regwatch.db.models import EntityType

_SLUG_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,38}[A-Z0-9]$")


class InvalidSlugError(ValueError):
    """Raised when a slug fails the regex check."""


class SlugConflictError(ValueError):
    """Raised when creating a slug that already exists (active or inactive)."""


@dataclass
class EntityTypeDTO:
    entity_type_id: int
    slug: str
    label: str
    cssf_entity_filter_id: int | None
    cssf_detail_labels: list[str] | None
    sort_order: int
    active: bool


def _to_dto(row: EntityType) -> EntityTypeDTO:
    return EntityTypeDTO(
        entity_type_id=row.entity_type_id,
        slug=row.slug,
        label=row.label,
        cssf_entity_filter_id=row.cssf_entity_filter_id,
        cssf_detail_labels=list(row.cssf_detail_labels) if row.cssf_detail_labels else None,
        sort_order=row.sort_order,
        active=row.active,
    )


# Sentinel must be defined BEFORE EntityTypeService because `update()`
# uses `_UNSET` as a default-argument value, which Python evaluates
# eagerly when the method def runs.
class _Unset:
    """Sentinel for update() so that None can mean 'clear the field'."""


_UNSET = _Unset()


class EntityTypeService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active(self) -> list[EntityTypeDTO]:
        rows = self._session.scalars(
            select(EntityType)
            .where(EntityType.active.is_(True))
            .order_by(EntityType.sort_order, EntityType.slug)
        ).all()
        return [_to_dto(r) for r in rows]

    def list_all(self) -> list[EntityTypeDTO]:
        rows = self._session.scalars(
            select(EntityType).order_by(EntityType.sort_order, EntityType.slug)
        ).all()
        return [_to_dto(r) for r in rows]

    def get_by_slug(self, slug: str) -> EntityTypeDTO | None:
        row = self._session.scalar(select(EntityType).where(EntityType.slug == slug))
        return _to_dto(row) if row else None

    def get(self, entity_type_id: int) -> EntityTypeDTO | None:
        row = self._session.get(EntityType, entity_type_id)
        return _to_dto(row) if row else None

    def create(
        self,
        *,
        slug: str,
        label: str,
        cssf_entity_filter_id: int | None = None,
        cssf_detail_labels: list[str] | None = None,
        sort_order: int = 100,
    ) -> EntityTypeDTO:
        if not _SLUG_RE.match(slug):
            raise InvalidSlugError(
                f"slug {slug!r} must match {_SLUG_RE.pattern} "
                "(3-40 chars, uppercase A-Z/0-9/_, starts with a letter, ends with letter or digit)"
            )
        if self._session.scalar(select(EntityType).where(EntityType.slug == slug)):
            raise SlugConflictError(f"slug {slug!r} already exists")
        row = EntityType(
            slug=slug,
            label=label,
            cssf_entity_filter_id=cssf_entity_filter_id,
            cssf_detail_labels=cssf_detail_labels or None,
            sort_order=sort_order,
        )
        self._session.add(row)
        self._session.flush()
        return _to_dto(row)

    def update(
        self,
        entity_type_id: int,
        *,
        label: str | None = None,
        cssf_entity_filter_id: int | None | _Unset = _UNSET,
        cssf_detail_labels: list[str] | None | _Unset = _UNSET,
        sort_order: int | None = None,
    ) -> EntityTypeDTO | None:
        row = self._session.get(EntityType, entity_type_id)
        if row is None:
            return None
        if label is not None:
            row.label = label
        if cssf_entity_filter_id is not _UNSET:
            row.cssf_entity_filter_id = cssf_entity_filter_id  # type: ignore[assignment]
        if cssf_detail_labels is not _UNSET:
            row.cssf_detail_labels = cssf_detail_labels  # type: ignore[assignment]
        if sort_order is not None:
            row.sort_order = sort_order
        self._session.flush()
        return _to_dto(row)

    def deactivate(self, entity_type_id: int) -> None:
        row = self._session.get(EntityType, entity_type_id)
        if row is not None:
            row.active = False
            self._session.flush()

    def reactivate(self, entity_type_id: int) -> None:
        row = self._session.get(EntityType, entity_type_id)
        if row is not None:
            row.active = True
            self._session.flush()


def prompt_segment(session: Session) -> str:
    """Return a bullet list of active entity-type slugs for inclusion in LLM prompts.

    The returned string ends with an ``"ALL"`` sentinel meaning "applies to
    all financial entities". Used by both the CSSF classifier
    (``services/discovery.py``) and the per-document classifier
    (``pipeline/match/classify.py``).
    """
    rows = session.scalars(
        select(EntityType)
        .where(EntityType.active.is_(True))
        .order_by(EntityType.sort_order, EntityType.slug)
    ).all()
    bullets = "\n".join(f'- "{r.slug}" ({r.label})' for r in rows)
    return (
        "Valid entity_type slugs:\n"
        f"{bullets}\n"
        '- "ALL" (applies broadly to all financial entities)'
    )
