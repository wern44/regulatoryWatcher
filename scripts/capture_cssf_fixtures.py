"""One-shot helper to capture rendered CSSF listing + detail fixtures.

Usage:
    python scripts/capture_cssf_fixtures.py

For each non-CSSF-circular publication type in config, fetches the live
CSSF listing page with entity_type + content_type numeric-ID query params
(server-side rendered; no JS required), and writes the HTML to
tests/fixtures/cssf/listing_aifms_<slug>.html. Then picks the first
listing row's detail URL and writes its body to
tests/fixtures/cssf/detail_<slug>_sample.html.

The CSSF regulatory-framework page honours ?entity_type=<id>&content_type=<id>
as server-side filter params (confirmed by data-href attributes in the rendered
DOM). No Playwright or JavaScript rendering is required.

Safe to re-run — overwrites fixtures in place.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config.example.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "cssf"
LISTING_URL = "https://www.cssf.lu/en/regulatory-framework/"
UA = "RegulatoryWatcher/1.0"
AIFM_ID = 502

# Skip CSSF circular — fixture already exists as listing_aifms_page1.html
SKIP_LABELS = {"CSSF circular"}

# Polite delay between HTTP requests (ms)
REQUEST_DELAY_MS = 700


def slugify(label: str) -> str:
    s = label.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def capture() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    pub_types = data["cssf_discovery"]["publication_types"]

    with httpx.Client(
        headers={"User-Agent": UA},
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        for pub in pub_types:
            label = pub["label"]
            if label in SKIP_LABELS:
                continue
            filter_id = int(pub["filter_id"])
            slug = slugify(label)

            print(f"Capturing {label} (id={filter_id}) ...", flush=True)

            # The CSSF regulatory-framework page accepts numeric entity_type and
            # content_type IDs as plain GET query parameters — server-rendered,
            # no JavaScript required.
            resp = client.get(
                LISTING_URL,
                params={
                    "entity_type": str(AIFM_ID),
                    "content_type": str(filter_id),
                },
            )
            resp.raise_for_status()
            html = resp.text

            listing_path = FIXTURES / f"listing_aifms_{slug}.html"
            listing_path.write_text(html, encoding="utf-8")
            print(f"  -> {listing_path.relative_to(REPO)}", flush=True)

            time.sleep(REQUEST_DELAY_MS / 1000)

            # Find first detail URL
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("li.library-element")
            print(f"  rows: {len(rows)}", flush=True)
            first = soup.select_one("li.library-element .library-element__title a")
            if first is None:
                print(
                    f"  [!] No library-element rows on {label} fixture; "
                    f"skipping detail-page capture."
                )
                continue

            href = first.get("href") or ""
            if not href:
                continue
            detail_url = (
                href
                if href.startswith("http")
                else f"https://www.cssf.lu{href}"
            )
            print(f"  detail URL: {detail_url}", flush=True)

            detail_resp = client.get(detail_url)
            detail_resp.raise_for_status()
            detail_path = FIXTURES / f"detail_{slug}_sample.html"
            detail_path.write_text(detail_resp.text, encoding="utf-8")
            print(f"  -> {detail_path.relative_to(REPO)}", flush=True)

            time.sleep(REQUEST_DELAY_MS / 1000)


if __name__ == "__main__":
    capture()
    print(
        "\nDone. Don't forget: git add tests/fixtures/cssf/listing_aifms_*.html "
        "tests/fixtures/cssf/detail_*_sample.html && commit."
    )
