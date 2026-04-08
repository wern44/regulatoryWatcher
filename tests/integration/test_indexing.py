from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

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
from regwatch.rag.indexing import index_version


def _session_with_vec(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    return Session(engine)


def _make_version(session: Session) -> DocumentVersion:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 18/698",
        title="IFM",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()

    v = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=1,
        is_current=True,
        fetched_at=datetime.now(timezone.utc),
        source_url="https://example.com",
        content_hash="x" * 64,
        html_text="First paragraph. Second paragraph about DORA and ICT risk.",
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(v)
    session.flush()
    return v


def test_index_version_writes_chunks_and_vectors(tmp_path: Path) -> None:
    session = _session_with_vec(tmp_path)
    version = _make_version(session)

    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [0.1, 0.2, 0.3, 0.4]

    index_version(
        session,
        version,
        ollama=fake_ollama,
        chunk_size_tokens=500,
        overlap_tokens=50,
        authorization_types=["AIFM", "CHAPTER15_MANCO"],
    )
    session.commit()

    chunks = session.query(DocumentChunk).all()
    assert len(chunks) >= 1

    count_vec = session.execute(
        text("SELECT COUNT(*) FROM document_chunk_vec")
    ).scalar()
    assert count_vec == len(chunks)
    count_fts = session.execute(
        text("SELECT COUNT(*) FROM document_chunk_fts")
    ).scalar()
    assert count_fts == len(chunks)
