"""Integration tests for CssfDiscoveryService._finalize_run status logic."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from regwatch.config import CssfDiscoveryConfig
from regwatch.db.models import (
    Base,
    DiscoveryRun,
    DiscoveryRunItem,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.services.cssf_discovery import CssfDiscoveryService


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _make_run(sf: sessionmaker[Session]) -> int:
    with sf() as s:
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


def _svc(sf: sessionmaker[Session]) -> CssfDiscoveryService:
    return CssfDiscoveryService(
        session_factory=sf,
        config=CssfDiscoveryConfig(publication_types=[]),
    )


def test_finalize_run_cell_exception_with_ok_items_is_partial(session_factory):
    """If one cell raises but others succeeded, status must be PARTIAL, not FAILED.

    This matters because Task 10 auto-retire gates on SUCCESS — a misclassified
    FAILED run skips retirement unnecessarily.
    """
    run_id = _make_run(session_factory)

    # Simulate 3 cells having produced UNCHANGED items, and 0 FAILED items.
    with session_factory() as s:
        for ref in ("CSSF 99/001", "CSSF 99/002", "CSSF 99/003"):
            s.add(DiscoveryRunItem(
                run_id=run_id,
                regulation_id=None,
                reference_number=ref,
                outcome="UNCHANGED",
                detail_url=None,
                entity_type="AIFM",
                content_type="CSSF circular",
                note=None,
            ))
        s.commit()

    svc = _svc(session_factory)
    # Simulate: one cell raised (aggregate_error is set) but no item was marked FAILED.
    svc._finalize_run(run_id, error="CHAPTER15_MANCO x Professional standard: timeout")

    with session_factory() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "PARTIAL", (
            f"expected PARTIAL when an aggregate error coexists with ok_count>0, "
            f"got {run.status}"
        )


def test_finalize_run_error_only_no_ok_items_is_failed(session_factory):
    """An aggregate error with zero OK items must yield FAILED."""
    run_id = _make_run(session_factory)

    svc = _svc(session_factory)
    svc._finalize_run(run_id, error="all cells exploded")

    with session_factory() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "FAILED"


def test_finalize_run_no_error_no_failed_items_is_success(session_factory):
    """Clean run with only OK items must be SUCCESS."""
    run_id = _make_run(session_factory)

    with session_factory() as s:
        s.add(DiscoveryRunItem(
            run_id=run_id,
            regulation_id=None,
            reference_number="CSSF 99/001",
            outcome="NEW",
            detail_url=None,
            entity_type="AIFM",
            content_type="CSSF circular",
            note=None,
        ))
        s.commit()

    svc = _svc(session_factory)
    svc._finalize_run(run_id, error=None)

    with session_factory() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "SUCCESS"


def test_finalize_run_failed_items_only_is_failed(session_factory):
    """FAILED items with no OK items and no aggregate error must be FAILED."""
    run_id = _make_run(session_factory)

    with session_factory() as s:
        s.add(DiscoveryRunItem(
            run_id=run_id,
            regulation_id=None,
            reference_number="CSSF 99/001",
            outcome="FAILED",
            detail_url=None,
            entity_type="AIFM",
            content_type="CSSF circular",
            note=None,
        ))
        s.commit()

    svc = _svc(session_factory)
    svc._finalize_run(run_id, error=None)

    with session_factory() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "FAILED"


def test_finalize_run_mixed_items_no_aggregate_error_is_partial(session_factory):
    """FAILED items alongside OK items without aggregate error must be PARTIAL."""
    run_id = _make_run(session_factory)

    with session_factory() as s:
        for ref, outcome in (("CSSF 99/001", "NEW"), ("CSSF 99/002", "FAILED")):
            s.add(DiscoveryRunItem(
                run_id=run_id,
                regulation_id=None,
                reference_number=ref,
                outcome=outcome,
                detail_url=None,
                entity_type="AIFM",
                content_type="CSSF circular",
                note=None,
            ))
        s.commit()

    svc = _svc(session_factory)
    svc._finalize_run(run_id, error=None)

    with session_factory() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "PARTIAL"


def test_retire_skipped_when_total_scraped_below_floor(session_factory):
    """A run that scraped fewer items than retire_min_scraped must not retire,
    even on SUCCESS — guards against silent scraper breakage.
    """
    sf = session_factory

    with sf() as s:
        run = DiscoveryRun(
            status="RUNNING", started_at=datetime.now(UTC),
            triggered_by="TEST", entity_types=["AIFM"], mode="full",
        )
        s.add(run)
        s.flush()
        # 2 items total -> below floor 10
        for ref in ("CSSF 99/A1", "CSSF 99/A2"):
            s.add(DiscoveryRunItem(
                run_id=run.run_id, regulation_id=None, reference_number=ref,
                outcome="UNCHANGED", detail_url=None,
                entity_type="AIFM", content_type="CSSF circular", note=None,
            ))
        # Existing row that WOULD be retired if the floor check wasn't there
        reg_orphan = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 99/GONE",
            title="x", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False, needs_review=False, url="", source_of_truth="CSSF_WEB",
        )
        s.add(reg_orphan)
        s.commit()
        run_id = run.run_id
        orphan_id = reg_orphan.regulation_id

    svc = CssfDiscoveryService(
        session_factory=sf,
        config=CssfDiscoveryConfig(retire_min_scraped=10, publication_types=[]),
    )
    svc._finalize_run(run_id, error=None)

    with sf() as s:
        run_after = s.get(DiscoveryRun, run_id)
        assert run_after.status == "SUCCESS"  # still SUCCESS; the gate is separate
        assert run_after.retired_count == 0
        assert run_after.error_summary is not None  # explains why retire was skipped
        orphan = s.get(Regulation, orphan_id)
        assert orphan.lifecycle_stage == LifecycleStage.IN_FORCE
