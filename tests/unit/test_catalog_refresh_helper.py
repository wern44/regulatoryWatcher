"""Test the shared helper used by both POST /catalog/refresh and the
scheduler's _scheduled_analysis job.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from regwatch.analysis.progress import AnalysisProgress
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.services.discovery_runner import run_catalog_refresh


def _session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _seed(session_factory, ref: str) -> None:
    with session_factory() as s:
        s.add(Regulation(
            reference_number=ref,
            type=RegulationType.CSSF_CIRCULAR,
            title=ref,
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            url="",
            source_of_truth="SEED",
        ))
        s.commit()


def test_run_catalog_refresh_finishes_success(tmp_path: Path) -> None:
    sf = _session_factory(tmp_path)
    _seed(sf, "CSSF 18/698")

    classify_reply = json.dumps({
        "is_ict": True,
        "dora_pillar": None,
        "applicable_entity_types": ["ALL"],
        "is_superseded": False,
        "superseded_by": None,
        "confidence": 0.9,
    })

    def chat(*a, system: str = "", **kw):
        if "missing" in (system or kw.get("system", "")).lower():
            return "[]"
        return classify_reply

    llm = MagicMock()
    llm.chat.side_effect = chat

    progress = AnalysisProgress()
    run_catalog_refresh(
        session_factory=sf,
        llm=llm,
        auth_types=["AIFM"],
        progress=progress,
    )

    assert progress.status == "SUCCESS"
    assert progress.finished_at is not None


def test_run_catalog_refresh_marks_aborted_when_cancelled(tmp_path: Path) -> None:
    sf = _session_factory(tmp_path)
    _seed(sf, "CSSF 18/698")
    _seed(sf, "CSSF 20/750")

    progress = AnalysisProgress()
    call_count = {"n": 0}

    def cancelling_chat(*a, **kw):
        call_count["n"] += 1
        progress.request_cancel()
        return json.dumps({
            "is_ict": False,
            "dora_pillar": None,
            "applicable_entity_types": ["ALL"],
            "is_superseded": False,
            "superseded_by": None,
            "confidence": 0.9,
        })

    llm = MagicMock()
    llm.chat.side_effect = cancelling_chat

    run_catalog_refresh(
        session_factory=sf, llm=llm, auth_types=["AIFM"], progress=progress
    )

    assert progress.status == "ABORTED"
    assert call_count["n"] == 1  # only one LLM call before cancel took effect


def test_run_catalog_refresh_marks_failed_on_unhandled_error(tmp_path: Path) -> None:
    sf = _session_factory(tmp_path)

    progress = AnalysisProgress()

    # Force an error from inside the worker that isn't caught by DiscoveryService
    # — e.g. session_factory itself raises.
    def boom():
        raise RuntimeError("db unavailable")

    run_catalog_refresh(
        session_factory=boom,
        llm=MagicMock(),
        auth_types=["AIFM"],
        progress=progress,
    )

    assert progress.status == "FAILED"
    assert progress.error is not None
    assert "db unavailable" in progress.error
