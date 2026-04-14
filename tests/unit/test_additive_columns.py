from sqlalchemy import create_engine, inspect
from regwatch.db.models import Base


def test_regulation_has_applicable_entity_types_and_chunk_has_heading_path():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("regulation")}
    assert "applicable_entity_types" in cols

    chunk_cols = {c["name"] for c in inspect(engine).get_columns("document_chunk")}
    assert "heading_path" in chunk_cols
