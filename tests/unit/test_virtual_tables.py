from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentChunk,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
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


def _setup_fts_fixture(tmp_path: Path) -> tuple:
    """Return (engine, regulation_id, version_id, chunk_id) after inserting one chunk."""
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=768)

    with Session(engine) as session:
        reg = Regulation(
            type=RegulationType.EU_REGULATION,
            reference_number="DORA",
            title="Digital Operational Resilience Act",
            issuing_authority="European Parliament",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=True,
            source_of_truth="SEED",
            url="https://example.com/dora",
        )
        session.add(reg)
        session.flush()

        v = DocumentVersion(
            regulation_id=reg.regulation_id,
            version_number=1,
            is_current=True,
            fetched_at=datetime.now(UTC),
            source_url="https://example.com/dora/v1",
            content_hash="d" * 64,
            pdf_is_protected=False,
            pdf_manual_upload=False,
        )
        session.add(v)
        session.flush()

        chunk = DocumentChunk(
            version_id=v.version_id,
            regulation_id=reg.regulation_id,
            chunk_index=0,
            text="DORA ICT risk management",
            token_count=4,
            lifecycle_stage="IN_FORCE",
            is_ict=True,
            authorization_types=[],
        )
        session.add(chunk)
        session.commit()
        return engine, reg.regulation_id, v.version_id, chunk.chunk_id


def test_fts5_inserts_sync_on_document_chunk_insert(tmp_path: Path) -> None:
    engine, _rid, _vid, _cid = _setup_fts_fixture(tmp_path)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT text FROM document_chunk_fts WHERE document_chunk_fts MATCH 'DORA'")
        ).fetchall()
    assert len(rows) == 1


def test_fts5_sync_on_document_chunk_delete(tmp_path: Path) -> None:
    engine, _rid, _vid, chunk_id = _setup_fts_fixture(tmp_path)

    with Session(engine) as session:
        chunk = session.get(DocumentChunk, chunk_id)
        assert chunk is not None
        session.delete(chunk)
        session.commit()

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT text FROM document_chunk_fts WHERE document_chunk_fts MATCH 'DORA'")
        ).fetchall()
    assert len(rows) == 0
