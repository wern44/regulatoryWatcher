from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import Base, ExtractionField


def test_seed_inserts_core_fields_idempotently():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_core_fields(s)
        s.commit()
        seed_core_fields(s)  # idempotent
        s.commit()
        names = [f.name for f in s.query(ExtractionField).all()]
        assert "main_points" in names
        assert "is_ict" in names
        assert "document_relationship" in names
        assert len(names) == len(set(names))
        ict = s.query(ExtractionField).filter_by(name="is_ict").one()
        assert ict.is_core is True
        assert ict.canonical_field == "is_ict"
