"""Live CSSF-website probe — excluded from default pytest runs.

Run manually with:
    pytest -m live tests/live/test_cssf_live_probe.py -v

Purpose: catch DOM regressions on the real site before they cascade
into a silent production discovery failure.

Note: the listing probe (parametrized by entity filter IDs) lives in the
Playwright-driven integration tests introduced in Task 5. Only the
detail-page parse check is kept here as it does not depend on the
listing mechanism.
"""
from __future__ import annotations

import httpx
import pytest

from regwatch.discovery.cssf_scraper import (
    fetch_circular_detail,
)

pytestmark = pytest.mark.live


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
