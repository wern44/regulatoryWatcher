"""End-to-end: matrix crawl -> provenance -> retire -> reactivate.

Three integration scenarios that exercise the full Task 8+10 path:

1. 6-cell matrix (2 entities × 3 pub types) creates 6 regulations with
   per-cell types and per-cell provenance rows.
2. A second run that drops one regulation retires it (REPEALED).
3. A third run that brings it back reactivates it (IN_FORCE).
"""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

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

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _listing_html(ref: str, slug: str) -> str:
    """Minimal listing page — exactly one ``li.library-element`` row."""
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


def _detail_html(ref: str) -> str:
    """Minimal detail page — enough for ``_parse_detail_html`` to succeed."""
    return f"""<!doctype html><html><head><title>{ref}</title></head><body>
<h1 class="single-news__title">Circular {ref}</h1>
<div class="content-header-info">Published on 1 January 2026</div>
<div class="single-news__subtitle"><p>Test circular content.</p></div>
<ul class="entities-list">
  <li>Alternative investment fund manager</li>
</ul>
<li class="related-document no-heading">
  <a href="https://www.cssf.lu/eng.pdf">PDF EN</a>
</li>
</body></html>"""


def _empty_listing_html() -> str:
    """A listing page with no ``li.library-element`` items — terminates pagination."""
    return "<html><body><ul></ul></body></html>"


# ---------------------------------------------------------------------------
# Transport factory
# ---------------------------------------------------------------------------


def _make_transport(
    cell_map: dict[tuple[str, str], tuple[str, str]],
) -> httpx.MockTransport:
    """Build a MockTransport that serves per-cell listing + detail HTML.

    *cell_map* maps ``(entity_type_id_str, content_type_id_str)`` to
    ``(ref, slug)``.  Any page-2+ pagination request returns the empty
    listing (stops paging).  Detail URLs are keyed by slug suffix.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)

        # Page 2+ — always empty to stop pagination.
        if "/en/regulatory-framework/page/" in path:
            return httpx.Response(200, text=_empty_listing_html())

        # First-page listing.
        if path in ("/en/regulatory-framework/", "/en/regulatory-framework"):
            cell_key = (params.get("entity_type", ""), params.get("content_type", ""))
            cell = cell_map.get(cell_key)
            if cell is None:
                return httpx.Response(200, text=_empty_listing_html())
            ref, slug = cell
            return httpx.Response(200, text=_listing_html(ref, slug))

        # Detail pages.
        if "/en/Document/" in path:
            for (_, _), (ref, slug) in cell_map.items():
                if slug in path:
                    return httpx.Response(200, text=_detail_html(ref))
            return httpx.Response(404)

        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _build_svc(
    sf: sessionmaker,
    cell_map: dict[tuple[str, str], tuple[str, str]],
    pub_types: list[PublicationTypeConfig],
) -> CssfDiscoveryService:
    cfg = CssfDiscoveryConfig(
        request_delay_ms=0,
        entity_filter_ids={"AIFM": 502, "CHAPTER15_MANCO": 2001},
        publication_types=pub_types,
        retire_min_scraped=0,  # disable the floor in tests that use tiny synthetic data
    )
    client = httpx.Client(
        transport=_make_transport(cell_map), base_url="https://www.cssf.lu"
    )
    return CssfDiscoveryService(session_factory=sf, config=cfg, http_client=client)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ENTITY_TYPES = [AuthorizationType.AIFM, AuthorizationType.CHAPTER15_MANCO]

# Three publication types: circular, regulation, professional standard.
_PUB_TYPES_3 = [
    PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
    PublicationTypeConfig(label="CSSF regulation", filter_id=600, type="CSSF_REGULATION"),
    PublicationTypeConfig(
        label="Professional standard", filter_id=620, type="PROFESSIONAL_STANDARD"
    ),
]


@pytest.fixture
def sf(tmp_path):
    engine = create_app_engine(tmp_path / "e2e.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Test 1 — 6-cell matrix creates 6 regulations with correct per-cell types
# ---------------------------------------------------------------------------

# 2 entities × 3 pub types = 6 cells.
# Layout: AIFM x {circ, reg, profstd}  +  CHAPTER15 x {circ, reg, profstd}
_CELL_MAP_6: dict[tuple[str, str], tuple[str, str]] = {
    ("502", "567"): ("CSSF 26/901", "circular-cssf-26-901"),       # AIFM x circ
    ("502", "600"): ("CSSF-REG 26/001", "cssf-reg-26-001"),        # AIFM x reg
    ("502", "620"): ("CSSF 26/903", "circular-cssf-26-903"),       # AIFM x profstd (slug-synth)
    ("2001", "567"): ("CSSF 26/904", "circular-cssf-26-904"),      # CH15 x circ
    ("2001", "600"): ("CSSF-REG 26/002", "cssf-reg-26-002"),       # CH15 x reg
    ("2001", "620"): ("CSSF 26/906", "circular-cssf-26-906"),      # CH15 x profstd
}


def test_full_matrix_creates_regulations_with_correct_types(sf):
    """6-cell matrix creates 6 Regulation rows; types derive from pub-type config."""
    svc = _build_svc(sf, _CELL_MAP_6, _PUB_TYPES_3)
    run_id = svc.run(
        entity_types=_ENTITY_TYPES,
        mode="full",
        triggered_by="TEST",
    )

    with sf() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "SUCCESS", f"run failed: {run.error_summary}"
        assert run.retired_count == 0, "no stale regs yet — nothing should be retired"

        regs = s.query(Regulation).filter(
            Regulation.source_of_truth == "CSSF_WEB"
        ).all()
        assert len(regs) == 6, f"expected 6 regulations, got {len(regs)}"

        by_ref = {r.reference_number: r for r in regs}

        # CSSF circular cells → CSSF_CIRCULAR.
        for ref in ("CSSF 26/901", "CSSF 26/904"):
            assert by_ref[ref].type == RegulationType.CSSF_CIRCULAR, (
                f"{ref} should be CSSF_CIRCULAR, got {by_ref[ref].type}"
            )

        # CSSF regulation cells → CSSF_REGULATION.
        for ref in ("CSSF-REG 26/001", "CSSF-REG 26/002"):
            assert by_ref[ref].type == RegulationType.CSSF_REGULATION, (
                f"{ref} should be CSSF_REGULATION, got {by_ref[ref].type}"
            )

        # Professional-standard cells → PROFESSIONAL_STANDARD.
        # The "Professional standard" pub type uses slug synthesis, so the ref
        # key is the synthesized slug-based ref rather than a CSSF regex match.
        # Find the two profstd rows by their type.
        profstd_rows = [r for r in regs if r.type == RegulationType.PROFESSIONAL_STANDARD]
        assert len(profstd_rows) == 2, (
            f"expected 2 PROFESSIONAL_STANDARD rows, got {len(profstd_rows)}: "
            f"{[r.reference_number for r in profstd_rows]}"
        )

    # Provenance: 6 RegulationDiscoverySource rows, one per CSSF_WEB reg per cell.
    with sf() as s:
        sources = s.query(RegulationDiscoverySource).all()
        # Each of the 6 CSSF_WEB regs appears in exactly one cell.
        assert len(sources) == 6, (
            f"expected 6 provenance rows, got {len(sources)}"
        )
        cell_keys = {(src.entity_type, src.content_type) for src in sources}
        assert cell_keys == {
            ("AIFM", "CSSF circular"),
            ("AIFM", "CSSF regulation"),
            ("AIFM", "Professional standard"),
            ("CHAPTER15_MANCO", "CSSF circular"),
            ("CHAPTER15_MANCO", "CSSF regulation"),
            ("CHAPTER15_MANCO", "Professional standard"),
        }


# ---------------------------------------------------------------------------
# Test 2 — Second run retires a regulation absent from all cells
# ---------------------------------------------------------------------------

# 2 entities × 1 pub type = 2 cells.  Two refs A and B.
_PUB_TYPES_1 = [
    PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
]

_CELL_MAP_AB: dict[tuple[str, str], tuple[str, str]] = {
    ("502", "567"): ("CSSF 26/801", "circular-cssf-26-801"),   # reg A
    ("2001", "567"): ("CSSF 26/802", "circular-cssf-26-802"),  # reg B
}

_CELL_MAP_A_ONLY: dict[tuple[str, str], tuple[str, str]] = {
    ("502", "567"): ("CSSF 26/801", "circular-cssf-26-801"),   # reg A only
    # CHAPTER15 cell returns empty — reg B is gone.
}


def test_second_run_retires_regulation_absent_from_all_cells(sf):
    """Phase-2 run drops reg B from its cell → B is retired; A stays IN_FORCE."""
    # Phase 1: both A and B are present.
    svc1 = _build_svc(sf, _CELL_MAP_AB, _PUB_TYPES_1)
    run1_id = svc1.run(entity_types=_ENTITY_TYPES, mode="full", triggered_by="TEST")

    with sf() as s:
        r1 = s.get(DiscoveryRun, run1_id)
        assert r1.status == "SUCCESS"
        assert r1.new_count == 2

    # Phase 2: only reg A is present.
    svc2 = _build_svc(sf, _CELL_MAP_A_ONLY, _PUB_TYPES_1)
    run2_id = svc2.run(entity_types=_ENTITY_TYPES, mode="full", triggered_by="TEST")

    with sf() as s:
        r2 = s.get(DiscoveryRun, run2_id)
        assert r2.status == "SUCCESS", f"run2 failed: {r2.error_summary}"
        assert r2.retired_count == 1, f"expected 1 retired, got {r2.retired_count}"

        # Reg A must remain IN_FORCE.
        reg_a = s.query(Regulation).filter(
            Regulation.reference_number == "CSSF 26/801"
        ).one()
        assert reg_a.lifecycle_stage == LifecycleStage.IN_FORCE, (
            f"reg A should stay IN_FORCE, got {reg_a.lifecycle_stage}"
        )

        # Reg B must be REPEALED.
        reg_b = s.query(Regulation).filter(
            Regulation.reference_number == "CSSF 26/802"
        ).one()
        assert reg_b.lifecycle_stage == LifecycleStage.REPEALED, (
            f"reg B should be REPEALED, got {reg_b.lifecycle_stage}"
        )

        # A DiscoveryRunItem with outcome="RETIRED" for reg B on run2.
        retired_items = s.query(DiscoveryRunItem).filter(
            DiscoveryRunItem.run_id == run2_id,
            DiscoveryRunItem.outcome == "RETIRED",
        ).all()
        assert len(retired_items) == 1, (
            f"expected 1 RETIRED item on run2, got {len(retired_items)}"
        )
        assert retired_items[0].reference_number == "CSSF 26/802"

        # A DiscoveryRunItem with outcome="UNCHANGED" for reg A on run2.
        unchanged_items = s.query(DiscoveryRunItem).filter(
            DiscoveryRunItem.run_id == run2_id,
            DiscoveryRunItem.outcome == "UNCHANGED",
        ).all()
        assert any(i.reference_number == "CSSF 26/801" for i in unchanged_items), (
            f"expected UNCHANGED item for CSSF 26/801 on run2, got: "
            f"{[i.reference_number for i in unchanged_items]}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Reactivation flips REPEALED back to IN_FORCE
# ---------------------------------------------------------------------------


def test_reactivation_flips_repealed_back_to_in_force(sf):
    """Phase-3 run sees reg B again → B is reactivated; first_seen_run_id preserved."""
    # Phase 1: both A and B.
    svc1 = _build_svc(sf, _CELL_MAP_AB, _PUB_TYPES_1)
    run1_id = svc1.run(entity_types=_ENTITY_TYPES, mode="full", triggered_by="TEST")

    # Phase 2: only A → B retires.
    svc2 = _build_svc(sf, _CELL_MAP_A_ONLY, _PUB_TYPES_1)
    run2_id = svc2.run(entity_types=_ENTITY_TYPES, mode="full", triggered_by="TEST")

    with sf() as s:
        r2 = s.get(DiscoveryRun, run2_id)
        assert r2.status == "SUCCESS"
        reg_b_before = s.query(Regulation).filter(
            Regulation.reference_number == "CSSF 26/802"
        ).one()
        assert reg_b_before.lifecycle_stage == LifecycleStage.REPEALED, (
            "pre-condition: reg B must be REPEALED before phase 3"
        )

    # Phase 3: both A and B are back.
    svc3 = _build_svc(sf, _CELL_MAP_AB, _PUB_TYPES_1)
    run3_id = svc3.run(entity_types=_ENTITY_TYPES, mode="full", triggered_by="TEST")

    with sf() as s:
        r3 = s.get(DiscoveryRun, run3_id)
        assert r3.status == "SUCCESS", f"run3 failed: {r3.error_summary}"

        # Reg B must be IN_FORCE again.
        reg_b = s.query(Regulation).filter(
            Regulation.reference_number == "CSSF 26/802"
        ).one()
        assert reg_b.lifecycle_stage == LifecycleStage.IN_FORCE, (
            f"reg B should be reactivated to IN_FORCE, got {reg_b.lifecycle_stage}"
        )

        # Provenance: last_seen_run_id updated to run3; first_seen_run_id still run1.
        src_b = s.query(RegulationDiscoverySource).filter(
            RegulationDiscoverySource.regulation_id == reg_b.regulation_id,
        ).first()
        assert src_b is not None, "reg B must have a RegulationDiscoverySource row"
        assert src_b.last_seen_run_id == run3_id, (
            f"last_seen_run_id should be run3 ({run3_id}), got {src_b.last_seen_run_id}"
        )
        assert src_b.first_seen_run_id == run1_id, (
            f"first_seen_run_id should still be run1 ({run1_id}), got {src_b.first_seen_run_id}"
        )
