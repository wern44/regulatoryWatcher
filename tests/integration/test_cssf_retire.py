"""Auto-retire + reactivation tests for the filter-matrix run."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from regwatch.config import CssfDiscoveryConfig, PublicationTypeConfig
from regwatch.db.models import (
    Base,
    DiscoveryRun,
    DiscoveryRunItem,
    LifecycleStage,
    Regulation,
    RegulationDiscoverySource,
    RegulationOverride,
    RegulationType,
)
from regwatch.services.cssf_discovery import CssfDiscoveryService


@pytest.fixture
def sf() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _stub_cfg() -> CssfDiscoveryConfig:
    return CssfDiscoveryConfig(
        entity_filter_ids={"AIFM": 502, "CHAPTER15_MANCO": 2001},
        publication_types=[
            PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
        ],
    )


def _mk_reg(s: Session, ref: str, *, source_of_truth: str = "CSSF_WEB",
            lifecycle: LifecycleStage = LifecycleStage.IN_FORCE) -> int:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=ref, title=ref,
        issuing_authority="CSSF",
        lifecycle_stage=lifecycle,
        is_ict=False, needs_review=False, url="",
        source_of_truth=source_of_truth,
    )
    s.add(reg)
    s.commit()
    return reg.regulation_id


def _mk_run(s: Session, status: str = "SUCCESS") -> int:
    run = DiscoveryRun(
        status=status, started_at=datetime.now(UTC),
        triggered_by="TEST", entity_types=["AIFM"], mode="full",
    )
    s.add(run); s.commit()
    return run.run_id


def _mk_source(s: Session, reg_id: int, run_id: int,
               entity: str = "AIFM", content: str = "CSSF circular") -> None:
    now = datetime.now(UTC)
    s.add(RegulationDiscoverySource(
        regulation_id=reg_id, entity_type=entity, content_type=content,
        first_seen_run_id=run_id, first_seen_at=now,
        last_seen_run_id=run_id, last_seen_at=now,
    ))
    s.commit()


def test_retire_marks_unseen_cssf_web_as_repealed(sf):
    with sf() as s:
        current_run = _mk_run(s, status="RUNNING")
        old_run = _mk_run(s, status="SUCCESS")
        # Seen in current run
        reg_a = _mk_reg(s, "CSSF 99/001")
        _mk_source(s, reg_a, current_run)
        # Seen only in an older run
        reg_b = _mk_reg(s, "CSSF 99/002")
        _mk_source(s, reg_b, old_run)
        # Never seen
        reg_c = _mk_reg(s, "CSSF 99/003")

    svc = CssfDiscoveryService(session_factory=sf, config=_stub_cfg())
    count = svc.retire_missing(current_run)
    assert count == 2

    with sf() as s:
        got = {r.reference_number: r.lifecycle_stage for r in s.scalars(select(Regulation)).all()}
        assert got["CSSF 99/001"] == LifecycleStage.IN_FORCE
        assert got["CSSF 99/002"] == LifecycleStage.REPEALED
        assert got["CSSF 99/003"] == LifecycleStage.REPEALED


def test_retire_respects_keep_active_override(sf):
    with sf() as s:
        run_id = _mk_run(s)
        reg_x = _mk_reg(s, "CSSF 99/555")
        # No discovery_source row -> would normally retire.
        s.add(RegulationOverride(
            reference_number="CSSF 99/555",
            action="KEEP_ACTIVE",
            reason="Manual keep",
            created_at=datetime.now(UTC),
        ))
        s.commit()

    svc = CssfDiscoveryService(session_factory=sf, config=_stub_cfg())
    count = svc.retire_missing(run_id)
    assert count == 0

    with sf() as s:
        got = s.get(Regulation, reg_x)
        assert got.lifecycle_stage == LifecycleStage.IN_FORCE


def test_retire_ignores_non_cssf_web_rows(sf):
    with sf() as s:
        run_id = _mk_run(s)
        seed_id = _mk_reg(s, "SEED 01", source_of_truth="SEED")
        disc_id = _mk_reg(s, "DISC 01", source_of_truth="DISCOVERED")
        stub_id = _mk_reg(s, "CSSF 99/STUB", source_of_truth="CSSF_STUB")

    svc = CssfDiscoveryService(session_factory=sf, config=_stub_cfg())
    count = svc.retire_missing(run_id)
    assert count == 0

    with sf() as s:
        for rid in (seed_id, disc_id, stub_id):
            assert s.get(Regulation, rid).lifecycle_stage == LifecycleStage.IN_FORCE


def test_retire_skipped_when_run_status_not_success(sf):
    """Auto-retire is gated on run.status == SUCCESS via _finalize_run.

    retire_missing itself is always safe to call directly; the gate is
    in _finalize_run. This test verifies the gate.
    """
    with sf() as s:
        run_id = _mk_run(s, status="RUNNING")
        reg_a = _mk_reg(s, "CSSF 99/001")
        # No discovery_source row -> would retire if the gate didn't fire.

    svc = CssfDiscoveryService(session_factory=sf, config=_stub_cfg())
    # Simulate finalize with an error -> status becomes PARTIAL/FAILED, retire skipped.
    svc._finalize_run(run_id, error="simulated cell failure")

    with sf() as s:
        got = s.get(Regulation, reg_a)
        assert got.lifecycle_stage == LifecycleStage.IN_FORCE
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "FAILED"  # error + no ok items
        assert run.retired_count == 0


def test_retire_writes_discovery_run_items(sf):
    with sf() as s:
        run_id = _mk_run(s, status="SUCCESS")
        reg_a = _mk_reg(s, "CSSF 99/A")
        reg_b = _mk_reg(s, "CSSF 99/B")
        # Neither seen in run -> both retire.

    svc = CssfDiscoveryService(session_factory=sf, config=_stub_cfg())
    svc.retire_missing(run_id)

    with sf() as s:
        items = s.scalars(
            select(DiscoveryRunItem).where(
                DiscoveryRunItem.run_id == run_id,
                DiscoveryRunItem.outcome == "RETIRED",
            )
        ).all()
        assert {i.reference_number for i in items} == {"CSSF 99/A", "CSSF 99/B"}


def test_reactivation_flips_repealed_to_in_force(sf):
    """When _reconcile_row sees an existing REPEALED CSSF_WEB row, flip to IN_FORCE."""
    from regwatch.discovery.cssf_scraper import CircularListingRow, CircularDetail
    from unittest.mock import patch

    with sf() as s:
        run_id = _mk_run(s)
        reg_id = _mk_reg(
            s, "CSSF 99/ZZZ",
            source_of_truth="CSSF_WEB",
            lifecycle=LifecycleStage.REPEALED,
        )

    listing = CircularListingRow(
        reference_number="CSSF 99/ZZZ", raw_title="CSSF 99/ZZZ",
        description="", publication_date=None,
        detail_url="https://www.cssf.lu/en/Document/circular-cssf-99-zzz/",
        publication_type_label="CSSF circular",
    )
    detail = CircularDetail(
        reference_number="CSSF 99/ZZZ", clean_title="CSSF 99/ZZZ",
        amended_by_refs=[], amends_refs=[], supersedes_refs=[],
        applicable_entities=[], pdf_url_en=None, pdf_url_fr=None,
        published_at=None, updated_at=None, description="",
    )
    svc = CssfDiscoveryService(session_factory=sf, config=_stub_cfg())
    pub = _stub_cfg().publication_types[0]
    from regwatch.db.models import AuthorizationType
    with patch("regwatch.services.cssf_discovery.fetch_circular_detail", return_value=detail):
        outcome = svc._reconcile_row(run_id, AuthorizationType.AIFM, pub, listing)

    with sf() as s:
        reg = s.get(Regulation, reg_id)
        assert reg.lifecycle_stage == LifecycleStage.IN_FORCE, (
            f"REPEALED -> IN_FORCE reactivation missing; got {reg.lifecycle_stage}"
        )


def test_retire_count_populates_discovery_run(sf):
    """_finalize_run on SUCCESS sets retired_count to the number retired."""
    with sf() as s:
        run_id = _mk_run(s, status="RUNNING")
        reg_seen = _mk_reg(s, "CSSF 99/S1")
        _mk_source(s, reg_seen, run_id)
        # Two unseen rows -> should retire on SUCCESS
        _mk_reg(s, "CSSF 99/R1")
        _mk_reg(s, "CSSF 99/R2")

    svc = CssfDiscoveryService(session_factory=sf, config=_stub_cfg())
    svc._finalize_run(run_id, error=None)  # clean -> SUCCESS -> retire runs

    with sf() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "SUCCESS"
        assert run.retired_count == 2
