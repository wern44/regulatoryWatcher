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


def _seed_lifecycle(db_file: Path) -> None:
    """Seed one IN_FORCE and one REPEALED regulation."""
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="LIVE 01",
            title="Live One",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        ))
        session.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="DEAD 01",
            title="Dead One",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.REPEALED,
            is_ict=False,
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        ))
        session.commit()


def test_catalog_defaults_to_in_force_only(tmp_path: Path, monkeypatch) -> None:
    """Without ?lifecycle= param, only IN_FORCE regs render."""
    client = _client(tmp_path, monkeypatch)
    _seed_lifecycle(tmp_path / "app.db")

    resp = client.get("/catalog")
    assert resp.status_code == 200
    assert "LIVE 01" in resp.text
    assert "DEAD 01" not in resp.text


def test_catalog_lifecycle_all_shows_everything(tmp_path: Path, monkeypatch) -> None:
    """?lifecycle=all shows both IN_FORCE and REPEALED."""
    client = _client(tmp_path, monkeypatch)
    _seed_lifecycle(tmp_path / "app.db")

    resp = client.get("/catalog?lifecycle=all")
    assert resp.status_code == 200
    assert "LIVE 01" in resp.text
    assert "DEAD 01" in resp.text


def test_catalog_lifecycle_repealed_filter(tmp_path: Path, monkeypatch) -> None:
    """?lifecycle=REPEALED shows only REPEALED regs."""
    client = _client(tmp_path, monkeypatch)
    _seed_lifecycle(tmp_path / "app.db")

    resp = client.get("/catalog?lifecycle=REPEALED")
    assert resp.status_code == 200
    assert "LIVE 01" not in resp.text
    assert "DEAD 01" in resp.text


def _seed_ict(sf) -> None:  # type: ignore[no-untyped-def]
    """Seed one ICT and one non-ICT in-force regulation."""
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="ICT 01",
            title="ICT One",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=True,
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        ))
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="GEN 01",
            title="Generic One",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        ))
        s.commit()


def test_catalog_ict_filter_only(tmp_path: Path, monkeypatch) -> None:
    """?ict=ict shows only ICT-flagged regulations."""
    client = _client(tmp_path, monkeypatch)
    _seed_ict(client.app.state.session_factory)

    resp = client.get("/catalog?ict=ict")
    assert resp.status_code == 200
    assert "ICT 01" in resp.text
    assert "GEN 01" not in resp.text


def test_catalog_ict_filter_non_ict(tmp_path: Path, monkeypatch) -> None:
    """?ict=non-ict shows only non-ICT regulations."""
    client = _client(tmp_path, monkeypatch)
    _seed_ict(client.app.state.session_factory)

    resp = client.get("/catalog?ict=non-ict")
    assert resp.status_code == 200
    assert "ICT 01" not in resp.text
    assert "GEN 01" in resp.text


def test_catalog_ict_default_shows_both(tmp_path: Path, monkeypatch) -> None:
    """No ict param -> both ICT and non-ICT in-force regs shown."""
    client = _client(tmp_path, monkeypatch)
    _seed_ict(client.app.state.session_factory)

    resp = client.get("/catalog")  # defaults: lifecycle=IN_FORCE, ict=any
    assert resp.status_code == 200
    assert "ICT 01" in resp.text
    assert "GEN 01" in resp.text
