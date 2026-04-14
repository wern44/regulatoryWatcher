from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from regwatch.cli import app
from regwatch.db.engine import create_app_engine
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import (
    Base, DocumentAnalysis, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)


def test_cli_analyse_by_reference(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    engine = create_app_engine(db)
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
            title="Risk mgmt", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="ICT circular text.",
        )
        s.add(v); seed_core_fields(s); s.commit()

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        open("config.example.yaml").read().replace(
            '"./data/app.db"', f'"{db.as_posix()}"'
        )
    )
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))

    fake_llm = MagicMock()
    fake_llm.chat.return_value = '{"is_ict": true, "keywords": ["ICT"]}'
    fake_llm.chat_model = "test-model"
    with patch("regwatch.cli._build_llm", return_value=fake_llm):
        result = CliRunner().invoke(app, ["analyse", "--reg", "CSSF 12/552"])
    assert result.exit_code == 0, result.output

    with sf() as s:
        analyses = s.query(DocumentAnalysis).all()
        assert len(analyses) == 1
        assert analyses[0].is_ict is True
