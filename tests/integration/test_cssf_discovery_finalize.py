"""Integration tests for CssfDiscoveryService._finalize_run status logic."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from regwatch.config import CssfDiscoveryConfig, PublicationTypeConfig
from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    AuthorizationType,
    Base,
    DiscoveryRun,
    DiscoveryRunItem,
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


# ---------------------------------------------------------------------------
# Regression: dry-run NEW path must write regulation_id=None for audit row
# ---------------------------------------------------------------------------


def _listing_html_dry(ref: str, slug: str) -> str:
    # Note: ref must match _REF_RE (numeric suffix) for "CSSF circular" pub type.
    return f"""<!doctype html><html><body>
<ul class="library-list">
  <li class="library-element">
    <div class="library-element__heading">
      <p class="library-element__dates">
        <span class="date--published">Published on 01.01.2026</span>
      </p>
    </div>
    <div class="library-element__main">
      <h3 class="library-element__title">
        <a href="/en/Document/{slug}/">{ref}</a>
      </h3>
    </div>
  </li>
</ul>
</body></html>"""


def _detail_html_dry(ref: str) -> str:
    return f"""<!doctype html><html><head><title>{ref}</title></head><body>
<h1 class="single-news__title">Circular {ref}</h1>
<div class="content-header-info">Published on 1 January 2026</div>
<div class="single-news__subtitle"><p>Dry-run regression test circular.</p></div>
<ul class="entities-list">
  <li>Alternative investment fund manager</li>
</ul>
<li class="related-document no-heading">
  <a href="https://www.cssf.lu/dry.pdf">PDF EN</a>
</li>
</body></html>"""


def _empty_listing_html_dry() -> str:
    return "<html><body><ul></ul></body></html>"


def test_dry_run_new_path_writes_null_regulation_id(tmp_path):
    """In dry-run mode, a NEW regulation must produce a DiscoveryRunItem with
    regulation_id=None (not the burned autoincrement id that was never committed).
    A non-None id would trigger FOREIGN KEY constraint failed in SQLite because
    the rolled-back Regulation row doesn't exist.
    """
    ref = "CSSF 26/999"
    slug = "circular-cssf-26-999"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/en/regulatory-framework/page/" in path:
            return httpx.Response(200, text=_empty_listing_html_dry())
        if path in ("/en/regulatory-framework/", "/en/regulatory-framework"):
            return httpx.Response(200, text=_listing_html_dry(ref, slug))
        if "/en/Document/" in path and slug in path:
            return httpx.Response(200, text=_detail_html_dry(ref))
        return httpx.Response(404)

    engine = create_app_engine(tmp_path / "dry_run_test.db")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)

    cfg = CssfDiscoveryConfig(
        request_delay_ms=0,
        entity_filter_ids={"AIFM": 502},
        publication_types=[
            PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
        ],
        retire_min_scraped=0,
    )
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://www.cssf.lu"
    )
    svc = CssfDiscoveryService(session_factory=sf, config=cfg, http_client=client)

    # Run in dry-run mode — must not raise FOREIGN KEY constraint failed.
    run_id = svc.run(
        entity_types=[AuthorizationType.AIFM],
        mode="full",
        triggered_by="TEST",
        dry_run=True,
    )

    with sf() as s:
        # The run must succeed (no FK exception propagated as FAILED).
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "SUCCESS", f"expected SUCCESS, got {run.status!r}: {run.error_summary}"

        # Exactly one DiscoveryRunItem with outcome NEW.
        items = s.query(DiscoveryRunItem).filter(
            DiscoveryRunItem.run_id == run_id,
            DiscoveryRunItem.outcome == "NEW",
        ).all()
        assert len(items) == 1, f"expected 1 NEW item, got {len(items)}"

        # The audit row must carry regulation_id=None — the regulation was
        # never committed, so pointing at its burned id would be an invalid FK.
        assert items[0].regulation_id is None, (
            f"dry-run NEW item must have regulation_id=None, "
            f"got {items[0].regulation_id}"
        )

        # No Regulation row must have been committed.
        regs = s.query(Regulation).all()
        assert len(regs) == 0, f"dry-run must not commit regulations, got {len(regs)}"

        # No provenance row must have been written (nothing to UPSERT against).
        sources = s.query(RegulationDiscoverySource).all()
        assert len(sources) == 0, (
            f"dry-run NEW path must not write RegulationDiscoverySource, got {len(sources)}"
        )


# ---------------------------------------------------------------------------
# Regression: preview_retire_candidates must use audit rows, not provenance
# ---------------------------------------------------------------------------


@pytest.fixture
def sf(tmp_path):
    """Session factory backed by a fresh on-disk SQLite DB."""
    engine = create_app_engine(tmp_path / "preview_test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_preview_retire_in_dry_run_excludes_observed_regs(sf):
    """preview_retire_candidates must exclude regs seen via DiscoveryRunItem
    audit, even when provenance wasn't persisted (dry-run)."""
    with sf() as s:
        run = DiscoveryRun(
            status="SUCCESS", started_at=datetime.now(UTC),
            triggered_by="TEST", entity_types=["AIFM"], mode="full",
        )
        s.add(run)
        s.flush()
        run_id = run.run_id
        # Two existing CSSF_WEB regs
        reg_seen = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 99/SEEN",
            title="x", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False, needs_review=False, url="", source_of_truth="CSSF_WEB",
        )
        reg_unseen = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 99/UNSEEN",
            title="x", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False, needs_review=False, url="", source_of_truth="CSSF_WEB",
        )
        s.add_all([reg_seen, reg_unseen])
        s.flush()
        # Audit: seen reg got an UNCHANGED item; unseen got nothing.
        # NO RegulationDiscoverySource rows (simulating dry-run).
        s.add(DiscoveryRunItem(
            run_id=run_id, regulation_id=reg_seen.regulation_id,
            reference_number="CSSF 99/SEEN", outcome="UNCHANGED",
            detail_url=None, entity_type="AIFM",
            content_type="CSSF circular", note=None,
        ))
        s.commit()

    svc = CssfDiscoveryService(
        session_factory=sf,
        config=CssfDiscoveryConfig(
            retire_min_scraped=0,
            publication_types=[
                PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
            ],
        ),
    )
    preview = svc.preview_retire_candidates(run_id)
    assert "CSSF 99/UNSEEN" in preview.candidates
    assert "CSSF 99/SEEN" not in preview.candidates
