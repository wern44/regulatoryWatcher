"""Live CSSF-website probe — excluded from default pytest runs.

Run manually with:
    pytest -m live tests/live/test_cssf_live_probe.py -v

Purpose: catch DOM / slug regressions on the real site before they cascade
into a silent production discovery failure.
"""
from __future__ import annotations

import itertools

import httpx
import pytest

from regwatch.discovery.cssf_scraper import (
    CircularListingRow,
    fetch_circular_detail,
    list_circulars,
)
from regwatch.services.cssf_discovery import CSSF_ENTITY_SLUGS

pytestmark = pytest.mark.live


@pytest.mark.parametrize("slug", sorted(set(CSSF_ENTITY_SLUGS.values())))
def test_listing_yields_at_least_one_row(slug: str) -> None:
    """Each configured entity slug should still return at least one listing row."""
    with httpx.Client(
        headers={"User-Agent": "RegulatoryWatcher/1.0 (live probe)"},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        rows = list(itertools.islice(
            list_circulars(slug, client=client, max_pages=1, request_delay_ms=0),
            20,
        ))
    assert rows, f"listing for slug {slug!r} returned zero rows — DOM or filter may have changed"
    for row in rows:
        assert isinstance(row, CircularListingRow)
        assert row.reference_number
        assert row.detail_url.startswith("http")


def test_detail_page_parses_known_circular() -> None:
    """A known-stable circular should still parse — title, PDF, amendment all present."""
    url = "https://www.cssf.lu/en/Document/circular-cssf-22-806/"
    with httpx.Client(
        headers={"User-Agent": "RegulatoryWatcher/1.0 (live probe)"},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        detail = fetch_circular_detail(url, client=client, request_delay_ms=0)
    assert detail.reference_number == "CSSF 22/806"
    # 22/806 was amended by 25/883 on 2025-04-09
    assert "CSSF 25/883" in detail.amended_by_refs, (
        "expected amendment relationship CSSF 22/806 ← CSSF 25/883 to still appear; "
        "if removed upstream, update this assertion"
    )
    assert detail.pdf_url_en and detail.pdf_url_en.endswith(".pdf")
    assert detail.published_at is not None
