"""CLI: regwatch keep-active REF — add/update KEEP_ACTIVE RegulationOverride."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from regwatch.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _seed_reg(tmp_path, ref="CSSF 99/TEST"):
    """Write a config + DB with one regulation so the command has something to look up."""
    from sqlalchemy.orm import sessionmaker

    from regwatch.db.engine import create_app_engine
    from regwatch.db.models import Base, LifecycleStage, Regulation, RegulationType

    db_path = tmp_path / "app.db"
    cfg_text = (
        open("config.example.yaml").read()
        .replace('"./data/app.db"', f'"{db_path.as_posix()}"')
    )
    (tmp_path / "pdfs").mkdir(exist_ok=True)
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "config.yaml").write_text(cfg_text, encoding="utf-8")

    eng = create_app_engine(db_path)
    Base.metadata.create_all(eng)
    sf = sessionmaker(eng, expire_on_commit=False)
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number=ref,
            title="Test Circular",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        ))
        s.commit()
    return tmp_path / "config.yaml", sf


def test_keep_active_adds_override(runner, tmp_path, monkeypatch):
    cfg_path, sf = _seed_reg(tmp_path)
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))

    result = runner.invoke(app, ["keep-active", "CSSF 99/TEST", "--reason", "testing"])
    assert result.exit_code == 0, result.output
    assert "CSSF 99/TEST" in result.output
    assert "KEEP_ACTIVE" in result.output

    from sqlalchemy import select

    from regwatch.db.models import RegulationOverride

    with sf() as s:
        rows = s.scalars(select(RegulationOverride)).all()
        assert len(rows) == 1
        assert rows[0].reference_number == "CSSF 99/TEST"
        assert rows[0].action == "KEEP_ACTIVE"
        assert rows[0].reason == "testing"


def test_keep_active_idempotent_updates_reason(runner, tmp_path, monkeypatch):
    cfg_path, sf = _seed_reg(tmp_path)
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))

    r1 = runner.invoke(app, ["keep-active", "CSSF 99/TEST", "--reason", "first reason"])
    assert r1.exit_code == 0
    r2 = runner.invoke(app, ["keep-active", "CSSF 99/TEST", "--reason", "second reason"])
    assert r2.exit_code == 0
    assert "Updated existing" in r2.output

    from sqlalchemy import select

    from regwatch.db.models import RegulationOverride

    with sf() as s:
        rows = s.scalars(select(RegulationOverride)).all()
        assert len(rows) == 1
        assert rows[0].reason == "second reason"


def test_keep_active_unknown_ref_exits_nonzero(runner, tmp_path, monkeypatch):
    cfg_path, _ = _seed_reg(tmp_path)
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))

    result = runner.invoke(app, ["keep-active", "CSSF 99/DOES-NOT-EXIST"])
    assert result.exit_code == 1
    assert "No regulation" in result.output
