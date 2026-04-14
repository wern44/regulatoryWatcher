from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

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
    cfg.write_text(open("config.example.yaml").read().replace(
        '"./data/app.db"', f'"{db.as_posix()}"'
    ))
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg))
    return db, sf


def test_cli_reindex_by_reference(tmp_path, monkeypatch):
    db, sf = _setup(tmp_path, monkeypatch)

    with sf() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
            title="Risk", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="Article 1\nICT obligations apply.",
        )
        s.add(v); s.commit()

    fake_llm = MagicMock()
    fake_llm.embed.return_value = [0.0] * 768
    fake_llm.chat_model = "test"

    with patch("regwatch.cli._build_llm", return_value=fake_llm):
        result = CliRunner().invoke(app, ["reindex", "--reg", "CSSF 12/552"])
    assert result.exit_code == 0, result.output

    with sf() as s:
        chunks = s.query(DocumentChunk).all()
        assert len(chunks) >= 1
        # Structure-aware chunker should have captured the Article heading path
        assert any(c.heading_path for c in chunks)


def test_cli_reindex_all(tmp_path, monkeypatch):
    db, sf = _setup(tmp_path, monkeypatch)

    with sf() as s:
        for ref in ("A/001", "A/002"):
            reg = Regulation(
                type=RegulationType.CSSF_CIRCULAR, reference_number=ref,
                title="t", issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
            )
            s.add(reg); s.flush()
            v = DocumentVersion(
                regulation_id=reg.regulation_id, version_number=1, is_current=True,
                fetched_at=datetime.now(UTC), source_url="x", content_hash=f"h{ref}",
                pdf_extracted_text=f"Content for {ref}.",
            )
            s.add(v); s.commit()

    fake_llm = MagicMock()
    fake_llm.embed.return_value = [0.0] * 768
    fake_llm.chat_model = "test"

    with patch("regwatch.cli._build_llm", return_value=fake_llm):
        result = CliRunner().invoke(app, ["reindex", "--all"])
    assert result.exit_code == 0, result.output

    with sf() as s:
        # Both regulations' versions should have chunks now
        chunks = s.query(DocumentChunk).all()
        version_ids_with_chunks = {c.version_id for c in chunks}
        assert len(version_ids_with_chunks) == 2


def test_cli_reindex_requires_flag(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["reindex"])
    # Neither --reg nor --all given → non-zero exit with message
    assert result.exit_code != 0
    assert "--reg" in result.output or "--all" in result.output
