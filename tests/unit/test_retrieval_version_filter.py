import struct
from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base, DocumentChunk, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters


def _setup(tmp_path):
    engine = create_app_engine(tmp_path / "db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    return engine


def _seed_two_versions(s: Session) -> tuple[int, int]:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR, reference_number="X", title="t",
        issuing_authority="x", lifecycle_stage=LifecycleStage.IN_FORCE,
        url="x", source_of_truth="SEED",
    )
    s.add(reg); s.flush()
    v_a = DocumentVersion(
        regulation_id=reg.regulation_id, version_number=1, is_current=False,
        fetched_at=datetime.now(UTC), source_url="x", content_hash="a",
    )
    v_b = DocumentVersion(
        regulation_id=reg.regulation_id, version_number=2, is_current=True,
        fetched_at=datetime.now(UTC), source_url="x", content_hash="b",
    )
    s.add_all([v_a, v_b]); s.flush()

    for v, content in [
        (v_a, "text of version one about risk"),
        (v_b, "text of version two about risk"),
    ]:
        chunk = DocumentChunk(
            version_id=v.version_id, regulation_id=reg.regulation_id,
            chunk_index=0, text=content, token_count=5,
            lifecycle_stage=LifecycleStage.IN_FORCE.value, is_ict=False,
            authorization_types=[],
        )
        s.add(chunk); s.flush()
        s.execute(
            sa_text("INSERT INTO document_chunk_vec(chunk_id, embedding) VALUES (:id, :vec)"),
            {"id": chunk.chunk_id, "vec": struct.pack("4f", 0.1, 0.1, 0.1, 0.1)},
        )
        s.execute(
            sa_text("INSERT INTO document_chunk_fts(rowid, text) VALUES (:id, :t)"),
            {"id": chunk.chunk_id, "t": content},
        )
    s.commit()
    return v_a.version_id, v_b.version_id


def test_version_ids_filter_excludes_other_versions(tmp_path):
    engine = _setup(tmp_path)
    with Session(engine) as s:
        v_a_id, v_b_id = _seed_two_versions(s)
        llm = MagicMock()
        llm.embed.return_value = [0.1, 0.1, 0.1, 0.1]
        retriever = HybridRetriever(s, ollama=llm, top_k=5)
        hits = retriever.retrieve("risk", RetrievalFilters(version_ids=[v_b_id]))
        assert hits, "expected hits for the scoped version"
        assert all(h.version_id == v_b_id for h in hits)


def test_version_ids_empty_list_is_unfiltered(tmp_path):
    engine = _setup(tmp_path)
    with Session(engine) as s:
        _seed_two_versions(s)
        llm = MagicMock()
        llm.embed.return_value = [0.1, 0.1, 0.1, 0.1]
        retriever = HybridRetriever(s, ollama=llm, top_k=5)
        hits = retriever.retrieve("risk", RetrievalFilters(version_ids=[]))
        # Empty filter = no restriction -> both chunks come back
        assert len({h.version_id for h in hits}) == 2
