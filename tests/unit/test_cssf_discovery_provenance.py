"""Provenance UPSERT semantics for the filter matrix."""
from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from regwatch.config import CssfDiscoveryConfig, PublicationTypeConfig
from regwatch.db.models import (
    Base,
    DiscoveryRun,
    LifecycleStage,
    Regulation,
    RegulationDiscoverySource,
    RegulationType,
)
from regwatch.services.cssf_discovery import CssfDiscoveryService


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _make_reg(s: Session, ref: str) -> int:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=ref,
        title="x",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        needs_review=False,
        url="",
        source_of_truth="CSSF_WEB",
    )
    s.add(reg)
    s.flush()
    return reg.regulation_id


def _make_run(s: Session) -> int:
    run = DiscoveryRun(
        status="RUNNING",
        started_at=datetime.now(UTC),
        triggered_by="TEST",
        entity_types=["AIFM"],
        mode="full",
    )
    s.add(run)
    s.commit()
    return run.run_id


def _stub_cfg() -> CssfDiscoveryConfig:
    return CssfDiscoveryConfig(
        entity_filter_ids={"AIFM": 502, "CHAPTER15_MANCO": 2001},
        publication_types=[
            PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
        ],
    )


def test_upsert_discovery_source_first_sight_inserts(session_factory):
    with session_factory() as s:
        reg_id = _make_reg(s, "CSSF 22/806")
        s.commit()
        run_id = _make_run(s)

    svc = CssfDiscoveryService(session_factory=session_factory, config=_stub_cfg())
    svc._upsert_discovery_source(
        run_id=run_id, regulation_id=reg_id,
        entity_type="AIFM", content_type="CSSF circular",
    )

    with session_factory() as s:
        src = s.scalars(select(RegulationDiscoverySource)).one()
        assert src.regulation_id == reg_id
        assert src.entity_type == "AIFM"
        assert src.content_type == "CSSF circular"
        assert src.first_seen_run_id == src.last_seen_run_id == run_id
        assert src.first_seen_at == src.last_seen_at


def test_upsert_discovery_source_second_sight_updates_last_seen_only(session_factory):
    with session_factory() as s:
        reg_id = _make_reg(s, "CSSF 22/806")
        s.commit()
        run1 = _make_run(s)

    svc = CssfDiscoveryService(session_factory=session_factory, config=_stub_cfg())
    svc._upsert_discovery_source(
        run_id=run1, regulation_id=reg_id,
        entity_type="AIFM", content_type="CSSF circular",
    )

    with session_factory() as s:
        first_seen_at_initial = s.scalars(select(RegulationDiscoverySource)).one().first_seen_at
        run2 = _make_run(s)

    # Small sleep so last_seen_at is strictly > first_seen_at
    time.sleep(0.01)

    svc._upsert_discovery_source(
        run_id=run2, regulation_id=reg_id,
        entity_type="AIFM", content_type="CSSF circular",
    )

    with session_factory() as s:
        rows = s.scalars(select(RegulationDiscoverySource)).all()
        assert len(rows) == 1, "must UPSERT, not insert a second row"
        src = rows[0]
        assert src.first_seen_run_id == run1
        assert src.first_seen_at == first_seen_at_initial
        assert src.last_seen_run_id == run2
        assert src.last_seen_at > first_seen_at_initial


def test_upsert_different_cells_insert_separate_rows(session_factory):
    """Same regulation seen in two cells -> two rows."""
    with session_factory() as s:
        reg_id = _make_reg(s, "CSSF 22/806")
        s.commit()
        run_id = _make_run(s)

    svc = CssfDiscoveryService(session_factory=session_factory, config=_stub_cfg())
    svc._upsert_discovery_source(
        run_id=run_id, regulation_id=reg_id,
        entity_type="AIFM", content_type="CSSF circular",
    )
    svc._upsert_discovery_source(
        run_id=run_id, regulation_id=reg_id,
        entity_type="CHAPTER15_MANCO", content_type="CSSF circular",
    )

    with session_factory() as s:
        rows = s.scalars(select(RegulationDiscoverySource)).all()
        assert len(rows) == 2
        etypes = {r.entity_type for r in rows}
        assert etypes == {"AIFM", "CHAPTER15_MANCO"}
