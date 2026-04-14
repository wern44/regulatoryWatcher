from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.analysis.fields import build_prompt_schema, coerce_value
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import Base, ExtractionFieldType


def test_build_prompt_schema_lists_active_fields_in_order():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_core_fields(s)
        s.commit()
        text = build_prompt_schema(s)
        assert "main_points" in text
        assert "is_ict" in text
        # Check ordering — main_points (10) before is_ict (40)
        assert text.index("main_points") < text.index("is_ict")
        assert "LONG_TEXT" in text
        assert "BOOL" in text


def test_coerce_bool():
    assert coerce_value(True, ExtractionFieldType.BOOL) is True
    assert coerce_value("true", ExtractionFieldType.BOOL) is True
    assert coerce_value("False", ExtractionFieldType.BOOL) is False
    assert coerce_value(0, ExtractionFieldType.BOOL) is False


def test_coerce_date():
    assert coerce_value("2026-01-17", ExtractionFieldType.DATE) == date(2026, 1, 17)
    assert coerce_value(None, ExtractionFieldType.DATE) is None


def test_coerce_list_text():
    assert coerce_value(["a", "b"], ExtractionFieldType.LIST_TEXT) == ["a", "b"]
    assert coerce_value("a, b, c", ExtractionFieldType.LIST_TEXT) == ["a", "b", "c"]
    assert coerce_value(None, ExtractionFieldType.LIST_TEXT) is None


def test_coerce_enum():
    assert coerce_value("NEW", ExtractionFieldType.ENUM) == "NEW"
    assert coerce_value("  replaces  ", ExtractionFieldType.ENUM) == "REPLACES"
