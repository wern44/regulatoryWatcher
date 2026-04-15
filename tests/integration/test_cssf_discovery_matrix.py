"""Integration tests for the 2×7 (entity × publication_type) filter matrix.

These tests exercise a 2×2 slice (2 entities × 2 publication types = 4 cells)
rather than the full 2×7 = 14 configured in production. The smaller matrix
sufficiently validates the iteration loop, per-cell provenance UPSERT,
Regulation.type derivation from config, and HTTP transport correctness
without multiplying fixture boilerplate. End-to-end coverage across all
7 publication types is Task 14's job (the full-scenario e2e test).
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
    Regulation,
    RegulationDiscoverySource,
    RegulationType,
)
from regwatch.services.cssf_discovery import CssfDiscoveryService

# ---------------------------------------------------------------------------
# Minimal HTML helpers
# ---------------------------------------------------------------------------

def _listing_html(ref: str, slug: str) -> str:
    """Return a minimal CSSF listing page with a single item for *ref*."""
    return f"""<!doctype html><html><body>
<ul class="library-list">
  <li class="library-element">
    <div class="library-element__title">
      <a href="/en/Document/{slug}/">{ref}</a>
    </div>
    <div class="library-element__subtitle">Test subtitle</div>
    <div class="date--published">Published on 01.01.2024</div>
  </li>
</ul>
</body></html>"""


def _detail_html(ref: str) -> str:
    """Return a minimal CSSF detail page for *ref*."""
    return f"""<!doctype html><html><head><title>{ref}</title></head><body>
<h1 class="post__title">{ref}</h1>
<div class="post__content">
  <p>Test description for {ref}.</p>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Matrix transport
# ---------------------------------------------------------------------------

# Mapping: (entity_type param, content_type param) -> (ref, slug)
_CELL_MAP = {
    ("502", "567"): ("CSSF 22/801", "circular-cssf-22-801"),   # AIFM x CSSF circular
    ("502", "600"): ("CSSF-REG 22/001", "cssf-reg-22-001"),    # AIFM x CSSF regulation
    ("2001", "567"): ("CSSF 22/802", "circular-cssf-22-802"),  # CHAPTER15 x CSSF circular
    ("2001", "600"): ("CSSF-REG 22/002", "cssf-reg-22-002"),   # CHAPTER15 x CSSF regulation
}


def _make_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)

        # Listing page (first page — no /page/N/ suffix)
        if path in ("/en/regulatory-framework/", "/en/regulatory-framework"):
            cell_key = (params.get("entity_type", ""), params.get("content_type", ""))
            cell = _CELL_MAP.get(cell_key)
            if cell is None:
                # Unknown cell — return empty so pagination stops
                return httpx.Response(200, text="<html><body></body></html>")
            ref, slug = cell
            return httpx.Response(200, text=_listing_html(ref, slug))

        # Pagination page 2+ for any cell — always empty so we stop
        if "/en/regulatory-framework/page/" in path:
            return httpx.Response(200, text="<html><body></body></html>")

        # Detail pages — keyed by slug
        if "/en/Document/" in path:
            # Find which cell this slug belongs to
            for (_et, _ct), (ref, slug) in _CELL_MAP.items():
                if slug in path:
                    return httpx.Response(200, text=_detail_html(ref))
            return httpx.Response(404)

        return httpx.Response(404)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sf(tmp_path):
    engine = create_app_engine(tmp_path / "app.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def svc(sf):
    cfg = CssfDiscoveryConfig(
        request_delay_ms=0,
        entity_filter_ids={"AIFM": 502, "CHAPTER15_MANCO": 2001},
        publication_types=[
            PublicationTypeConfig(
                label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR",
            ),
            PublicationTypeConfig(
                label="CSSF regulation", filter_id=600, type="CSSF_REGULATION",
            ),
        ],
        retire_min_scraped=0,  # disable floor in tests that use tiny synthetic data
    )
    client = httpx.Client(transport=_make_transport(), base_url="https://www.cssf.lu")
    return CssfDiscoveryService(session_factory=sf, config=cfg, http_client=client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_matrix_creates_four_regulations(sf, svc):
    run_id = svc.run(
        entity_types=[AuthorizationType.AIFM, AuthorizationType.CHAPTER15_MANCO],
        mode="full",
        triggered_by="TEST",
    )
    with sf() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status == "SUCCESS", f"run failed: {run.error_summary}"
        assert run.new_count == 4

        regs = s.query(Regulation).all()
        assert len(regs) == 4, f"expected 4 regulations, got {len(regs)}"


def test_matrix_creates_four_discovery_source_rows(sf, svc):
    svc.run(
        entity_types=[AuthorizationType.AIFM, AuthorizationType.CHAPTER15_MANCO],
        mode="full",
        triggered_by="TEST",
    )
    with sf() as s:
        sources = s.query(RegulationDiscoverySource).all()
        assert len(sources) == 4, (
            f"expected 4 discovery source rows, got {len(sources)}; "
            f"cells: {[(r.entity_type, r.content_type) for r in sources]}"
        )


def test_matrix_regulation_types_match_pub_type_config(sf, svc):
    """Regulation.type is derived from the cell's PublicationTypeConfig.type."""
    svc.run(
        entity_types=[AuthorizationType.AIFM, AuthorizationType.CHAPTER15_MANCO],
        mode="full",
        triggered_by="TEST",
    )
    with sf() as s:
        regs = s.query(Regulation).all()
        by_ref = {r.reference_number: r for r in regs}
        # CSSF circular cells → CSSF_CIRCULAR
        for ref in ("CSSF 22/801", "CSSF 22/802"):
            assert by_ref[ref].type == RegulationType.CSSF_CIRCULAR, (
                f"{ref} should be CSSF_CIRCULAR, got {by_ref[ref].type}"
            )
        # CSSF regulation cells → CSSF_REGULATION
        for ref in ("CSSF-REG 22/001", "CSSF-REG 22/002"):
            assert by_ref[ref].type == RegulationType.CSSF_REGULATION, (
                f"{ref} should be CSSF_REGULATION, got {by_ref[ref].type}"
            )


def test_matrix_discovery_source_entity_and_content_types(sf, svc):
    """Each source row carries the correct entity_type and content_type."""
    svc.run(
        entity_types=[AuthorizationType.AIFM, AuthorizationType.CHAPTER15_MANCO],
        mode="full",
        triggered_by="TEST",
    )
    with sf() as s:
        sources = s.query(RegulationDiscoverySource).all()
        cell_keys = {(src.entity_type, src.content_type) for src in sources}
        assert cell_keys == {
            ("AIFM", "CSSF circular"),
            ("AIFM", "CSSF regulation"),
            ("CHAPTER15_MANCO", "CSSF circular"),
            ("CHAPTER15_MANCO", "CSSF regulation"),
        }
