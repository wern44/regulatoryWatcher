from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed(db_file: Path) -> None:
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        def add(ref: str, auth: str) -> None:
            reg = Regulation(
                type=RegulationType.CSSF_CIRCULAR,
                reference_number=ref,
                title=ref,
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=False,
                source_of_truth="SEED",
                url="https://example.com",
            )
            reg.applicabilities.append(
                RegulationApplicability(authorization_type=auth)
            )
            session.add(reg)

        add("CSSF 18/698", "BOTH")
        add("CSSF 23/844", "AIFM")
        add("CSSF 11/512", "CHAPTER15_MANCO")
        session.commit()


def test_catalog_lists_all(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")

    r = client.get("/catalog")
    assert r.status_code == 200
    assert "CSSF 18/698" in r.text
    assert "CSSF 23/844" in r.text
    assert "CSSF 11/512" in r.text


def test_catalog_filters_by_aifm(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")

    r = client.get("/catalog?authorization=AIFM")
    assert r.status_code == 200
    # AIFM + BOTH should appear, MANCO-only should not.
    assert "CSSF 18/698" in r.text
    assert "CSSF 23/844" in r.text
    assert "CSSF 11/512" not in r.text
