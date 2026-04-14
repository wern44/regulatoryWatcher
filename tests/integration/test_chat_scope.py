import struct
from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy import text as sa_text

from regwatch.db.models import (
    DocumentChunk,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed_two_versions(c):
    """Seed a single regulation with two indexed versions."""
    dim = c.app.state.config.llm.embedding_dim
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 12/552",
            title="Risk",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x",
            source_of_truth="SEED",
        )
        s.add(reg)
        s.flush()
        v1 = DocumentVersion(
            regulation_id=reg.regulation_id,
            version_number=1,
            is_current=False,
            fetched_at=datetime.now(UTC),
            source_url="x",
            content_hash="h1",
        )
        v2 = DocumentVersion(
            regulation_id=reg.regulation_id,
            version_number=2,
            is_current=True,
            fetched_at=datetime.now(UTC),
            source_url="x",
            content_hash="h2",
        )
        s.add_all([v1, v2])
        s.flush()

        for v, body in [
            (v1, "The 2018 version describes legacy obligations for AIFMs."),
            (v2, "The 2024 version adds ICT risk management duties for AIFMs."),
        ]:
            chunk = DocumentChunk(
                version_id=v.version_id,
                regulation_id=reg.regulation_id,
                chunk_index=0,
                text=body,
                token_count=20,
                lifecycle_stage=LifecycleStage.IN_FORCE.value,
                is_ict=False,
                authorization_types=[],
            )
            s.add(chunk)
            s.flush()
            vec = struct.pack(f"{dim}f", *[0.1] * dim)
            s.execute(
                sa_text(
                    "INSERT INTO document_chunk_vec(chunk_id, embedding) VALUES (:id, :vec)"
                ),
                {"id": chunk.chunk_id, "vec": vec},
            )
            s.execute(
                sa_text(
                    "INSERT INTO document_chunk_fts(rowid, text) VALUES (:id, :t)"
                ),
                {"id": chunk.chunk_id, "t": body},
            )
        s.commit()
        return v1.version_id, v2.version_id


def _install_mock_llm(c):
    """Install a deterministic mock LLM and return it for assertion inspection."""
    dim = c.app.state.config.llm.embedding_dim
    llm = MagicMock()
    llm.embed.return_value = [0.1] * dim
    llm.chat_model = "mock"

    # Echo back the context so the test can see which chunks were retrieved.
    def _chat(**kwargs):
        user_msg = kwargs.get("user", "")
        return f"ANSWER: {user_msg[:800]}"

    llm.chat.side_effect = _chat
    c.app.state.llm_client = llm
    return llm


def test_chat_scoped_to_version_includes_only_that_versions_chunks(
    tmp_path, monkeypatch
):
    c = _client(tmp_path, monkeypatch)
    _install_mock_llm(c)
    v1_id, _v2_id = _seed_two_versions(c)

    # Scope to the OLD version only
    r = c.post(
        "/chat/ask",
        data={"query": "What are the obligations?", "version_ids": [str(v1_id)]},
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)
    text = r.text
    assert "2018" in text, (
        f"expected 2018 snippet in scoped answer, got: {text[:400]}"
    )
    assert "2024" not in text, (
        f"2024 snippet should be excluded, got: {text[:400]}"
    )


def test_chat_no_scope_sees_both_versions(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _install_mock_llm(c)
    _seed_two_versions(c)

    r = c.post(
        "/chat/ask",
        data={"query": "What are the obligations?"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)
    text = r.text
    assert "2018" in text or "2024" in text
