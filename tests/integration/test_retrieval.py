from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.rag.indexing import index_version
from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters


def _setup(tmp_path: Path) -> tuple[Session, DocumentVersion]:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    session = Session(engine)

    reg = Regulation(
        type=RegulationType.EU_REGULATION,
        reference_number="DORA",
        title="DORA",
        issuing_authority="EU",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=True,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()

    version = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=1,
        is_current=True,
        fetched_at=datetime.now(timezone.utc),
        source_url="https://example.com",
        content_hash="y" * 64,
        html_text=(
            "DORA sets ICT risk management requirements. "
            "Article 24 TLPT rules apply. "
            "Third-party ICT risk register is mandatory."
        ),
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(version)
    session.flush()

    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [1.0, 0.0, 0.0, 0.0]
    index_version(
        session,
        version,
        ollama=fake_ollama,
        chunk_size_tokens=200,
        overlap_tokens=20,
        authorization_types=["AIFM", "CHAPTER15_MANCO"],
    )
    session.commit()
    return session, version


def test_dense_and_sparse_both_find_chunks(tmp_path: Path) -> None:
    session, _version = _setup(tmp_path)
    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [1.0, 0.0, 0.0, 0.0]

    retriever = HybridRetriever(session, ollama=fake_ollama, top_k=5)
    hits = retriever.retrieve("Article 24 TLPT", RetrievalFilters())

    assert len(hits) >= 1
    assert any("Article 24" in h.text or "TLPT" in h.text for h in hits)


def test_ict_filter_applies(tmp_path: Path) -> None:
    session, _version = _setup(tmp_path)
    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [1.0, 0.0, 0.0, 0.0]

    retriever = HybridRetriever(session, ollama=fake_ollama, top_k=5)
    hits = retriever.retrieve("ICT", RetrievalFilters(is_ict=True))
    assert all(h.is_ict for h in hits)
