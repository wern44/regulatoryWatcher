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


def test_index_version_keeps_the_keyword_index_consistent(tmp_path: Path) -> None:
    """FTS rows are written by the document_chunk triggers; writing them a
    second time from index_version made the FTS integrity check fail."""
    session = _session_with_vec(tmp_path)
    version = _make_version(session)
    ollama = MagicMock()
    ollama.embed.return_value = [0.1, 0.2, 0.3, 0.4]

    index_version(
        session, version, ollama=ollama,
        chunk_size_tokens=50, overlap_tokens=0, authorization_types=["AIFM"],
    )
    session.commit()

    session.execute(
        text("INSERT INTO document_chunk_fts(document_chunk_fts, rank) "
             "VALUES ('integrity-check', 1)")
    )
    hits = session.execute(
        text("SELECT count(*) FROM document_chunk_fts WHERE document_chunk_fts MATCH 'DORA'")
    ).scalar()
    assert hits == 1


def test_index_pending_versions_indexes_only_unindexed_ones(tmp_path: Path) -> None:
    from regwatch.rag.indexing import index_pending_versions

    session = _session_with_vec(tmp_path)
    version = _make_version(session)
    session.commit()
    ollama = MagicMock()
    ollama.embed.return_value = [0.1, 0.2, 0.3, 0.4]

    first = index_pending_versions(
        session, ollama=ollama,
        chunk_size_tokens=50, overlap_tokens=0, authorization_types=["AIFM"],
    )
    second = index_pending_versions(
        session, ollama=ollama,
        chunk_size_tokens=50, overlap_tokens=0, authorization_types=["AIFM"],
    )

    assert first == 1
    assert second == 0
    assert session.query(DocumentChunk).filter_by(version_id=version.version_id).count() > 0


def test_index_pending_versions_stops_when_embeddings_fail(tmp_path: Path) -> None:
    from regwatch.rag.indexing import index_pending_versions

    session = _session_with_vec(tmp_path)
    _make_version(session)
    session.commit()
    ollama = MagicMock()
    ollama.embed.side_effect = RuntimeError("LLM server down")

    assert index_pending_versions(
        session, ollama=ollama,
        chunk_size_tokens=50, overlap_tokens=0, authorization_types=["AIFM"],
    ) == 0
    assert session.query(DocumentChunk).count() == 0
