from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed(db_file: Path) -> None:
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Regulation(
                type=RegulationType.EU_REGULATION,
                reference_number="DORA_REG_XYZ",
                title="Digital Operational Resilience Act",
                issuing_authority="EU",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=True,
                source_of_truth="SEED",
                url="https://example.com",
            )
        )
        session.add(
            Regulation(
                type=RegulationType.EU_DIRECTIVE,
                reference_number="AIFMD II",
                title="AIFMD II draft",
                issuing_authority="EU",
                lifecycle_stage=LifecycleStage.ADOPTED_NOT_IN_FORCE,
                is_ict=False,
                source_of_truth="SEED",
                url="https://example.com",
                application_date=date.today() + timedelta(days=100),
            )
        )
        session.commit()


def test_drafts_view(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")
    r = client.get("/drafts")
    assert r.status_code == 200
    assert "AIFMD II" in r.text
    assert "DORA_REG_XYZ" not in r.text  # DORA_REG_XYZ is IN_FORCE, not a draft


def test_deadlines_view(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")
    r = client.get("/deadlines")
    assert r.status_code == 200
    assert "AIFMD II" in r.text
    assert "AMBER" in r.text  # 100 days -> AMBER band


def test_ict_view(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")
    r = client.get("/ict")
    assert r.status_code == 200
    assert "DORA_REG_XYZ" in r.text
    assert "AIFMD II" not in r.text
