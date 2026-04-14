import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import Base, ExtractionFieldType
from regwatch.services.extraction_fields import (
    ExtractionFieldDTO,
    ExtractionFieldService,
    FieldProtectedError,
)


def _svc() -> tuple[ExtractionFieldService, Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_core_fields(s)
    s.commit()
    return ExtractionFieldService(s), s


def test_list_all_fields():
    svc, _ = _svc()
    rows = svc.list()
    assert len(rows) == 12
    assert all(isinstance(r, ExtractionFieldDTO) for r in rows)
    assert rows[0].display_order <= rows[-1].display_order


def test_add_custom_field_and_get_by_id():
    svc, _ = _svc()
    row = svc.create(
        name="severity", label="Severity", description="How severe?",
        data_type=ExtractionFieldType.TEXT, enum_values=None, display_order=200,
    )
    assert row.is_core is False
    assert svc.get(row.field_id).name == "severity"


def test_delete_user_field_ok_core_forbidden():
    svc, _ = _svc()
    custom = svc.create(
        name="severity", label="Severity", description="x",
        data_type=ExtractionFieldType.TEXT, enum_values=None, display_order=200,
    )
    svc.delete(custom.field_id)

    core_id = svc.list()[0].field_id
    with pytest.raises(FieldProtectedError):
        svc.delete(core_id)


def test_update_locks_core_immutable_columns():
    svc, _ = _svc()
    ict = next(f for f in svc.list() if f.name == "is_ict")
    svc.update(ict.field_id, label="ICT?", description="updated prompt", is_active=False)
    # name + data_type + canonical_field unchanged
    got = svc.get(ict.field_id)
    assert got.name == "is_ict"
    assert got.data_type is ExtractionFieldType.BOOL
    assert got.canonical_field == "is_ict"
    assert got.label == "ICT?"
    assert got.description == "updated prompt"
    assert got.is_active is False

    # Attempting to change name on a core field raises
    with pytest.raises(FieldProtectedError):
        svc.update(ict.field_id, name="not_allowed")
