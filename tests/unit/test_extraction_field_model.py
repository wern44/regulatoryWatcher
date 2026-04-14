from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import Base, ExtractionField, ExtractionFieldType


def test_extraction_field_round_trip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        f = ExtractionField(
            name="main_points",
            label="Main Points",
            description="Summarize the key obligations in 3-5 bullets.",
            data_type=ExtractionFieldType.LONG_TEXT,
            is_core=True,
            is_active=True,
            canonical_field=None,
            display_order=10,
        )
        s.add(f)
        s.commit()
        got = s.query(ExtractionField).filter_by(name="main_points").one()
        assert got.data_type is ExtractionFieldType.LONG_TEXT
        assert got.is_core is True
