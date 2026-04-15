"""Live probe: discover FacetWP content_type slugs on cssf.lu.

Run explicitly:  pytest -m live tests/live/test_cssf_slug_discovery.py -v -s

Prints each (label, slug) pair so you can update config.example.yaml.
Fails noisily if any of the seven required labels is missing.
"""
from __future__ import annotations

import httpx
import pytest
from bs4 import BeautifulSoup

LISTING_URL = "https://www.cssf.lu/en/regulatory-framework/"

REQUIRED_LABELS: list[str] = [
    "CSSF circular",
    "CSSF regulation",
    "Law",
    "Grand-ducal regulation",
    "Ministerial regulation",
    "Annex to a CSSF circular",
    "Professional standard",
]


@pytest.mark.live
def test_fwp_content_type_slugs_are_discoverable() -> None:
    with httpx.Client(
        headers={"User-Agent": "RegulatoryWatcher/1.0"},
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        resp = client.get(LISTING_URL)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # FacetWP renders the facet as a <select> with data-name attributes or a
    # class-qualified container. The production DOM at time of writing uses
    # `.facetwp-facet-content_type` (kebab vs snake varies by facet name).
    # Try both because FacetWP normalizes the name inconsistently.
    candidates = [
        ".facetwp-facet-content_type option",
        ".facetwp-facet-content-type option",
        "select.facetwp-dropdown option[data-facet='content_type']",
    ]
    selects: list = []
    for sel in candidates:
        selects = soup.select(sel)
        if selects:
            break
    assert selects, (
        "Could not find FacetWP content_type options on listing page; "
        f"tried selectors: {candidates}. Inspect the page source to "
        "determine the current selector."
    )

    discovered: dict[str, str] = {}
    for opt in selects:
        label = opt.get_text(strip=True)
        value = opt.get("value") or ""
        slug = value[0] if isinstance(value, list) else value
        if label and slug:
            discovered[label] = str(slug)

    print("\n=== Discovered FacetWP content_type slugs ===")
    for k, v in sorted(discovered.items()):
        print(f"  {k!r:40s} -> {v!r}")

    missing = [lbl for lbl in REQUIRED_LABELS if lbl not in discovered]
    assert not missing, (
        f"Missing expected labels from FacetWP content_type facet: {missing}\n"
        f"Full discovered mapping: {discovered}"
    )
