import struct
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from sqlalchemy import text as sa_text
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from regwatch.cli import app
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


def _setup(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    engine = create_app_engine(db)
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=768)
    sf = sessionmaker(engine, expire_on_commit=False)

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        open("config.example.yaml").read().replace(
            '"./data/app.db"', f'"{db.as_posix()}"'
        )
    )
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg))
    return db, sf


def _seed_two_versions(sf) -> tuple[int, int, int]:
    """Seed one regulation with two versions, each with one chunk, full FTS + vec."""
    with sf() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 12/552",
            title="t",
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
            (v1, "The 2018 version describes legacy obligations about risk."),
            (v2, "The 2024 version adds ICT risk management duties."),
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
                heading_path=["Article 1"],
            )
            s.add(chunk)
            s.flush()
            vec = struct.pack("768f", *([0.1] * 768))
            s.execute(
                sa_text(
                    "INSERT INTO document_chunk_vec(chunk_id, embedding) "
                    "VALUES (:id, :vec)"
                ),
                {"id": chunk.chunk_id, "vec": vec},
            )
            s.execute(
                sa_text(
                    "INSERT INTO document_chunk_fts(rowid, text) "
                    "VALUES (:id, :t)"
                ),
                {"id": chunk.chunk_id, "t": body},
            )
        s.commit()
        return reg.regulation_id, v1.version_id, v2.version_id


def _mock_llm():
    llm = MagicMock()
    llm.embed.return_value = [0.1] * 768
    llm.chat_model = "test"
    # Echo user message so the test can inspect which context was passed.
    llm.chat.side_effect = lambda **kw: f"ANSWER: {kw.get('user', '')[:600]}"
    return llm


def test_cli_chat_version_flag_filters_context(tmp_path, monkeypatch):
    _, sf = _setup(tmp_path, monkeypatch)
    _, v1, _ = _seed_two_versions(sf)

    llm = _mock_llm()
    with patch("regwatch.cli._build_llm", return_value=llm):
        result = CliRunner().invoke(
            app, ["chat", "What is required?", "--version", str(v1)]
        )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "2018" in out, f"expected 2018 snippet in output, got: {out[:400]}"
    assert "2024" not in out, f"2024 snippet should be excluded, got: {out[:400]}"


def test_cli_chat_reg_flag_expands_to_current_version(tmp_path, monkeypatch):
    _, sf = _setup(tmp_path, monkeypatch)
    _seed_two_versions(sf)

    llm = _mock_llm()
    with patch("regwatch.cli._build_llm", return_value=llm):
        result = CliRunner().invoke(
            app, ["chat", "What is required?", "--reg", "CSSF 12/552"]
        )
    assert result.exit_code == 0, result.output
    # --reg expands to the CURRENT version (v2 = 2024)
    out = result.output
    assert "2024" in out
    assert "2018" not in out, f"v1 should be excluded, got: {out[:400]}"


def test_cli_chat_no_flags_is_unscoped(tmp_path, monkeypatch):
    _, sf = _setup(tmp_path, monkeypatch)
    _seed_two_versions(sf)

    llm = _mock_llm()
    with patch("regwatch.cli._build_llm", return_value=llm):
        result = CliRunner().invoke(app, ["chat", "What is required?"])
    assert result.exit_code == 0, result.output
    # Without filters, both versions' snippets are available to be retrieved
    out = result.output
    assert "2018" in out or "2024" in out


def test_chat_answer_cites_heading_path_when_available(tmp_path, monkeypatch):
    _, sf = _setup(tmp_path, monkeypatch)
    _seed_two_versions(sf)  # chunks have heading_path=["Article 1"]

    llm = _mock_llm()
    with patch("regwatch.cli._build_llm", return_value=llm):
        result = CliRunner().invoke(app, ["chat", "What is required?"])
    assert result.exit_code == 0, result.output
    # The context and/or the citations trailer should mention Article 1
    # since chunks have heading_path=["Article 1"].
    assert "Article 1" in result.output
