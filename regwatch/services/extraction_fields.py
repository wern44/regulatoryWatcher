"""Service for CRUD on ExtractionField with core-field protection."""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from regwatch.db.models import ExtractionField, ExtractionFieldType


class FieldProtectedError(RuntimeError):
    """Raised when a user tries to delete or alter a locked attribute of a core field."""


class FieldNotFoundError(LookupError):
    """Raised when a field_id doesn't match any row."""


class FieldNameConflictError(ValueError):
    """Raised when create() is called with a name that already exists."""


@dataclass
class ExtractionFieldDTO:
    field_id: int
    name: str
    label: str
    description: str
    data_type: ExtractionFieldType
    enum_values: list[str] | None
    is_core: bool
    is_active: bool
    canonical_field: str | None
    display_order: int


class ExtractionFieldService:
    _NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

    def __init__(self, session: Session) -> None:
        self._s = session

    def list(self) -> list[ExtractionFieldDTO]:
        rows = (
            self._s.query(ExtractionField)
            .order_by(ExtractionField.display_order, ExtractionField.name)
            .all()
        )
        return [self._to_dto(r) for r in rows]

    def get(self, field_id: int) -> ExtractionFieldDTO:
        row = self._s.query(ExtractionField).filter_by(field_id=field_id).one_or_none()
        if row is None:
            raise FieldNotFoundError(f"No extraction field with id {field_id}")
        return self._to_dto(row)

    def create(
        self,
        *,
        name: str,
        label: str,
        description: str,
        data_type: ExtractionFieldType,
        enum_values: list[str] | None,
        display_order: int,
    ) -> ExtractionFieldDTO:
        if not self._NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"Invalid field name {name!r}: must match {self._NAME_PATTERN.pattern}"
            )
        existing = (
            self._s.query(ExtractionField).filter_by(name=name).one_or_none()
        )
        if existing is not None:
            raise FieldNameConflictError(
                f"Field with name {name!r} already exists"
            )
        row = ExtractionField(
            name=name,
            label=label,
            description=description,
            data_type=data_type,
            enum_values=enum_values,
            is_core=False,
            is_active=True,
            canonical_field=None,
            display_order=display_order,
        )
        self._s.add(row)
        self._s.flush()
        return self._to_dto(row)

    def update(self, field_id: int, **changes: object) -> ExtractionFieldDTO:
        row = self._s.query(ExtractionField).filter_by(field_id=field_id).one_or_none()
        if row is None:
            raise FieldNotFoundError(f"No extraction field with id {field_id}")
        locked_for_core = {"name", "data_type", "canonical_field", "is_core"}
        if row.is_core:
            for k in changes.keys() & locked_for_core:
                raise FieldProtectedError(
                    f"Cannot change '{k}' on core field '{row.name}'"
                )
        for k, v in changes.items():
            setattr(row, k, v)
        self._s.flush()
        return self._to_dto(row)

    def delete(self, field_id: int) -> None:
        row = self._s.query(ExtractionField).filter_by(field_id=field_id).one_or_none()
        if row is None:
            raise FieldNotFoundError(f"No extraction field with id {field_id}")
        if row.is_core:
            raise FieldProtectedError(f"Cannot delete core field '{row.name}'")
        self._s.delete(row)
        self._s.flush()

    @staticmethod
    def _to_dto(row: ExtractionField) -> ExtractionFieldDTO:
        return ExtractionFieldDTO(
            field_id=row.field_id,
            name=row.name,
            label=row.label,
            description=row.description,
            data_type=row.data_type,
            enum_values=row.enum_values,
            is_core=row.is_core,
            is_active=row.is_active,
            canonical_field=row.canonical_field,
            display_order=row.display_order,
        )
