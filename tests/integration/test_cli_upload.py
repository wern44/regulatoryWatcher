from unittest.mock import MagicMock, patch

from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from regwatch.cli import app
from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base, DocumentChunk, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables


def test_cli_upload_creates_version(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    engine = create_app_engine(db)
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=768)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="X", title="t",
            issuing_authority="CSSF", lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x", source_of_truth="SEED",
        )
        s.add(reg); s.commit()
        rid = reg.regulation_id

    cfg = tmp_path / "config.yaml"
    cfg.write_text(open("config.example.yaml").read().replace(
        '"./data/app.db"', f'"{db.as_posix()}"'
    ))
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg))

    html = tmp_path / "doc.html"
    html.write_text("<html><body><p>Hello ICT</p></body></html>")

    fake_llm = MagicMock()
    fake_llm.embed.return_value = [0.0] * 768
    fake_llm.chat_model = "test"
    with patch("regwatch.cli._build_llm", return_value=fake_llm):
        result = CliRunner().invoke(app, ["upload", "--reg", "X", str(html)])
    assert result.exit_code == 0, result.output
    with sf() as s:
        versions = s.query(DocumentVersion).filter_by(regulation_id=rid).all()
        assert len(versions) == 1
        assert versions[0].pdf_manual_upload is True
        chunks = s.query(DocumentChunk).filter_by(
            version_id=versions[0].version_id
        ).count()
        assert chunks > 0


def test_cli_upload_unknown_reference_fails_clean(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    engine = create_app_engine(db)
    Base.metadata.create_all(engine)

    cfg = tmp_path / "config.yaml"
    cfg.write_text(open("config.example.yaml").read().replace(
        '"./data/app.db"', f'"{db.as_posix()}"'
    ))
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg))

    html = tmp_path / "doc.html"
    html.write_text("<html><body>x</body></html>")

    result = CliRunner().invoke(app, ["upload", "--reg", "NONEXISTENT", str(html)])
    assert result.exit_code != 0
    assert "NONEXISTENT" in result.output or "No regulation" in result.output
