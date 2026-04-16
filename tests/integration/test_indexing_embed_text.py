from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base, DocumentChunk, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.rag.indexing import index_version


def _seed_version(s: Session, body: str) -> DocumentVersion:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
        title="Risk", issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
    )
    s.add(reg); s.flush()
    v = DocumentVersion(
        regulation_id=reg.regulation_id, version_number=1, is_current=True,
        fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
        pdf_extracted_text=body,
    )
    s.add(v); s.commit()
    return v


def _build(tmp_path):
    engine = create_app_engine(tmp_path / "app.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    return engine


def test_embed_receives_prefixed_text_but_chunk_stores_original(tmp_path):
    engine = _build(tmp_path)
    text = (
        "Chapter I — Overview\n\n"
        "Article 1\n"
        "This provision addresses ICT risk."
    )
    with Session(engine) as s:
        v = _seed_version(s, text)

        captured: list[str] = []
        llm = MagicMock()
        def _embed(t: str):
            captured.append(t)
            return [0.0, 0.0, 0.0, 0.0]
        llm.embed.side_effect = _embed

        index_version(
            s, v, ollama=llm,
            chunk_size_tokens=1000, overlap_tokens=50,
            authorization_types=["AIFM"],
        )
        s.commit()

        chunks = s.query(DocumentChunk).all()
        assert chunks
        # Stored text is the ORIGINAL paragraph (no metadata prefix)
        for c in chunks:
            assert not c.text.startswith("[")
        # At least one chunk covers the Article 1 body
        article_chunks = [c for c in chunks if "ICT risk" in c.text]
        assert article_chunks

        # The embedder saw a METADATA-PREFIXED form for the structured chunk.
        # New format: "CSSF 12/552 — Risk — CSSF, Chapter I, Article 1:\n..."
        prefixed = [t for t in captured if "Article 1" in t and "CSSF 12/552" in t]
        assert prefixed, f"embed should receive prefixed text, got: {captured[:2]}"

        # heading_path is persisted on the structured chunk
        assert article_chunks[0].heading_path
        assert any("Article 1" in h for h in article_chunks[0].heading_path)


def test_unstructured_text_stores_empty_heading_path_and_unprefixed_embed(tmp_path):
    engine = _build(tmp_path)
    text = "Just a paragraph with no structure whatsoever, talking about things."
    with Session(engine) as s:
        v = _seed_version(s, text)

        captured: list[str] = []
        llm = MagicMock()
        def _embed(t: str):
            captured.append(t); return [0.0, 0.0, 0.0, 0.0]
        llm.embed.side_effect = _embed

        index_version(
            s, v, ollama=llm,
            chunk_size_tokens=1000, overlap_tokens=50, authorization_types=[],
        )
        s.commit()

        chunks = s.query(DocumentChunk).all()
        assert chunks
        for c in chunks:
            assert c.heading_path == [] or c.heading_path is None
        # Unstructured text still gets regulation_meta prefix but no heading path.
        # It should NOT have bracket-style heading prefixes.
        for t in captured:
            assert "[" not in t.split("\n")[0] or "Chapter" not in t.split("\n")[0]
