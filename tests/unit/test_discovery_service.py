"""Tests for LLM-driven regulation discovery and classification."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from regwatch.analysis.progress import AnalysisProgress
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationOverride,
    RegulationType,
)
from regwatch.services.discovery import DiscoveryService


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)()


def _add_regulation(
    session: Session, ref: str, *, is_ict: bool = False, source: str = "SEED"
) -> Regulation:
    reg = Regulation(
        reference_number=ref,
        type=RegulationType.CSSF_CIRCULAR,
        title=f"Test regulation {ref}",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=is_ict,
        url=f"https://example.com/{ref}",
        source_of_truth=source,
    )
    session.add(reg)
    session.flush()
    return reg


def test_classify_updates_ict_flag(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_regulation(session, "CSSF 18/698")

    llm = MagicMock()
    llm.chat.return_value = json.dumps({
        "is_ict": True,
        "dora_pillar": "THIRD_PARTY_RISK",
        "applicable_entity_types": ["ALL"],
        "is_superseded": False,
        "superseded_by": None,
        "confidence": 0.95,
    })

    svc = DiscoveryService(session, llm=llm)
    count = svc.classify_catalog()
    session.commit()

    session.refresh(reg)
    assert reg.is_ict is True
    assert reg.dora_pillar.value == "THIRD_PARTY_RISK"
    assert reg.needs_review is False
    assert count == 1


def test_classify_non_ict(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_regulation(session, "CSSF 23/844", is_ict=True)

    llm = MagicMock()
    llm.chat.return_value = json.dumps({
        "is_ict": False,
        "dora_pillar": None,
        "applicable_entity_types": ["AIFM"],
        "is_superseded": False,
        "superseded_by": None,
        "confidence": 0.9,
    })

    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog()
    session.commit()

    session.refresh(reg)
    assert reg.is_ict is False
    assert reg.dora_pillar is None


def test_override_set_ict_wins(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_regulation(session, "CSSF 18/698", is_ict=False)

    session.add(RegulationOverride(
        regulation_id=reg.regulation_id,
        reference_number="CSSF 18/698",
        action="SET_ICT",
        created_at=datetime.now(UTC),
    ))
    session.flush()

    llm = MagicMock()
    llm.chat.return_value = json.dumps({
        "is_ict": False,
        "dora_pillar": None,
        "applicable_entity_types": ["ALL"],
        "is_superseded": False,
        "superseded_by": None,
        "confidence": 0.9,
    })

    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog()
    session.commit()

    session.refresh(reg)
    assert reg.is_ict is True  # Override wins over LLM


def test_override_unset_ict_wins(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_regulation(session, "CSSF 18/698", is_ict=True)

    session.add(RegulationOverride(
        regulation_id=reg.regulation_id,
        reference_number="CSSF 18/698",
        action="UNSET_ICT",
        created_at=datetime.now(UTC),
    ))
    session.flush()

    llm = MagicMock()
    llm.chat.return_value = json.dumps({
        "is_ict": True,
        "dora_pillar": "THIRD_PARTY_RISK",
        "applicable_entity_types": ["ALL"],
        "is_superseded": False,
        "superseded_by": None,
        "confidence": 0.9,
    })

    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog()
    session.commit()

    session.refresh(reg)
    assert reg.is_ict is False  # Override wins


def test_low_confidence_flags_needs_review(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_regulation(session, "CSSF 18/698")

    llm = MagicMock()
    llm.chat.return_value = json.dumps({
        "is_ict": True,
        "dora_pillar": None,
        "applicable_entity_types": ["ALL"],
        "is_superseded": False,
        "superseded_by": None,
        "confidence": 0.5,
    })

    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog()
    session.commit()

    session.refresh(reg)
    assert reg.needs_review is True


def test_discover_missing_adds_new_regulations(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_regulation(session, "CSSF 18/698")

    llm = MagicMock()
    llm.chat.return_value = json.dumps([
        {
            "reference_number": "CSSF 20/750",
            "title": "Requirements on ICT risk management",
            "issuing_authority": "CSSF",
            "type": "CSSF_CIRCULAR",
            "is_ict": True,
            "dora_pillar": "ICT_RISK_MGMT",
            "url": "https://cssf.lu/20-750",
            "applicability": "BOTH",
        }
    ])

    svc = DiscoveryService(session, llm=llm)
    added = svc.discover_missing(["AIFM", "CHAPTER15_MANCO"])
    session.commit()

    assert added == 1
    new = session.query(Regulation).filter(
        Regulation.reference_number == "CSSF 20/750"
    ).one()
    assert new.is_ict is True
    assert new.source_of_truth == "DISCOVERED"
    assert new.needs_review is True


def test_discover_skips_excluded(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_regulation(session, "CSSF 18/698")

    session.add(RegulationOverride(
        reference_number="CSSF 20/750",
        action="EXCLUDE",
        created_at=datetime.now(UTC),
    ))
    session.flush()

    llm = MagicMock()
    llm.chat.return_value = json.dumps([
        {
            "reference_number": "CSSF 20/750",
            "title": "Requirements on ICT risk management",
            "issuing_authority": "CSSF",
            "type": "CSSF_CIRCULAR",
            "is_ict": True,
            "dora_pillar": "ICT_RISK_MGMT",
            "url": "https://cssf.lu/20-750",
            "applicability": "BOTH",
        }
    ])

    svc = DiscoveryService(session, llm=llm)
    added = svc.discover_missing(["AIFM"])
    session.commit()

    assert added == 0


def test_discover_skips_existing(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_regulation(session, "CSSF 18/698")

    llm = MagicMock()
    llm.chat.return_value = json.dumps([
        {
            "reference_number": "CSSF 18/698",
            "title": "Already exists",
            "issuing_authority": "CSSF",
            "type": "CSSF_CIRCULAR",
            "is_ict": True,
            "url": "",
        }
    ])

    svc = DiscoveryService(session, llm=llm)
    added = svc.discover_missing(["AIFM"])
    assert added == 0


def test_classify_handles_llm_error(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_regulation(session, "CSSF 18/698")

    llm = MagicMock()
    llm.chat.side_effect = RuntimeError("LLM down")

    svc = DiscoveryService(session, llm=llm)
    count = svc.classify_catalog()
    assert count == 0  # No updates when LLM fails


def test_discover_missing_parses_fenced_json_reply(tmp_path: Path) -> None:
    session = _session(tmp_path)
    llm = MagicMock()
    llm.chat.return_value = '''```json
[
  {"reference_number": "CSSF 20/750",
   "title": "ICT and security risk management",
   "issuing_authority": "CSSF",
   "type": "CSSF_CIRCULAR",
   "is_ict": true,
   "dora_pillar": "ICT_RISK_MGMT",
   "url": "https://example.test/20-750"}
]
```'''
    svc = DiscoveryService(session, llm=llm)
    added = svc.discover_missing(["AIFM"])
    session.commit()
    assert added == 1
    reg = session.query(Regulation).filter_by(reference_number="CSSF 20/750").one()
    assert reg.is_ict is True
    assert reg.source_of_truth == "DISCOVERED"


def test_discover_missing_tolerates_prose_before_json(tmp_path: Path) -> None:
    session = _session(tmp_path)
    llm = MagicMock()
    llm.chat.return_value = (
        "Here are some missing regulations you might want to include:\n"
        '[{"reference_number": "CSSF 22/806", "title": "Outsourcing arrangements", '
        '"issuing_authority": "CSSF", "type": "CSSF_CIRCULAR", "is_ict": true}]'
    )
    svc = DiscoveryService(session, llm=llm)
    added = svc.discover_missing(["AIFM"])
    session.commit()
    assert added == 1


def test_discover_missing_returns_0_on_unparseable_reply(tmp_path: Path) -> None:
    session = _session(tmp_path)
    llm = MagicMock()
    llm.chat.return_value = "I'm not sure, please ask again."
    svc = DiscoveryService(session, llm=llm)
    added = svc.discover_missing(["AIFM"])
    session.commit()
    assert added == 0


def test_classify_catalog_ticks_progress_per_regulation(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_regulation(session, "CSSF 18/698")
    _add_regulation(session, "CSSF 20/750")

    llm = MagicMock()
    llm.chat.return_value = json.dumps({
        "is_ict": True,
        "dora_pillar": None,
        "applicable_entity_types": ["ALL"],
        "is_superseded": False,
        "superseded_by": None,
        "confidence": 0.9,
    })

    progress = AnalysisProgress()
    progress.start(run_id=0, total=2)
    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog(progress=progress)

    # Each regulation should have ticked the progress bar.
    assert progress.done == 2
    assert progress.current_label is not None
    assert "CSSF" in progress.current_label


def test_classify_catalog_aborts_on_cancel(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_regulation(session, "CSSF 18/698")
    _add_regulation(session, "CSSF 20/750")
    _add_regulation(session, "CSSF 22/806")

    progress = AnalysisProgress()
    progress.start(run_id=0, total=3)

    call_count = {"n": 0}

    def fake_chat(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate user pressing Abort after the first regulation.
            progress.request_cancel()
        return json.dumps({
            "is_ict": True,
            "dora_pillar": None,
            "applicable_entity_types": ["ALL"],
            "is_superseded": False,
            "superseded_by": None,
            "confidence": 0.9,
        })

    llm = MagicMock()
    llm.chat.side_effect = fake_chat

    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog(progress=progress)

    # After cancel, the loop should not call the LLM for the remaining 2.
    assert call_count["n"] == 1


def test_discover_missing_skips_when_cancelled(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_regulation(session, "CSSF 18/698")

    progress = AnalysisProgress()
    progress.start(run_id=0, total=1)
    progress.request_cancel()

    llm = MagicMock()
    llm.chat.return_value = json.dumps([
        {"reference_number": "CSSF 99/999", "title": "x", "type": "CSSF_CIRCULAR"}
    ])

    svc = DiscoveryService(session, llm=llm)
    added = svc.discover_missing(["AIFM"], progress=progress)

    # Cancelled before LLM call — no LLM invocation, nothing added.
    assert added == 0
    assert llm.chat.call_count == 0
