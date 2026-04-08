from pathlib import Path

from sqlalchemy import text

from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base
from regwatch.db.virtual_tables import create_virtual_tables


def test_creates_vec_and_fts_tables(tmp_path: Path) -> None:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=768)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ).scalars().all()
        assert "document_chunk_vec" in rows
        assert "document_chunk_fts" in rows


def test_create_virtual_tables_is_idempotent(tmp_path: Path) -> None:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=768)
    # Second call must not raise.
    create_virtual_tables(engine, embedding_dim=768)
