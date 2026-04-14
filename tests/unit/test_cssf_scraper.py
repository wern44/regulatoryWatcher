"""Tests for the CSSF scraper -- fixtures-driven, no network."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from regwatch.discovery.cssf_scraper import (
    CircularListingRow,
    CircularNotFoundError,
    _parse_detail_html,
    _parse_listing_html,
    fetch_circular_detail,
    list_circulars,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cssf"


def test_parse_listing_yields_rows() -> None:
    html = (FIXTURES / "listing_aifms_page1.html").read_text(encoding="utf-8")
    rows = list(_parse_listing_html(html))
    assert len(rows) > 0, "listing page should produce at least one row"
    ref_pat = re.compile(r"CSSF[\s-]?[A-Z]*\s*\d{2,4}[/-]\d{1,4}", re.IGNORECASE)
    for r in rows:
        assert isinstance(r, CircularListingRow)
        assert ref_pat.match(r.reference_number), f"bad ref: {r.reference_number!r}"
        assert r.detail_url.startswith("http")
        # Detail URL should point at the CSSF /Document/ space.
        assert "/Document/" in r.detail_url or "/document/" in r.detail_url


def test_parse_listing_extracts_publication_date_and_description() -> None:
    html = (FIXTURES / "listing_aifms_page1.html").read_text(encoding="utf-8")
    rows = list(_parse_listing_html(html))
    # At least one row should have a non-empty description and a parsable date.
    assert any(r.description for r in rows)
    assert any(isinstance(r.publication_date, date) for r in rows)


def test_parse_detail_22_806_extracts_amendment_and_entities() -> None:
    html = (FIXTURES / "detail_22_806.html").read_text(encoding="utf-8")
    d = _parse_detail_html(html, source_url="https://example/circular-cssf-22-806/")

    assert d.reference_number == "CSSF 22/806"
    # "(as amended by Circular CSSF 25/883)" should be parsed out
    assert "CSSF 25/883" in d.amended_by_refs
    # clean_title must not contain "(as amended ...)" parenthetical.
    assert "as amended" not in d.clean_title.lower()
    assert "Circular CSSF 22/806" in d.clean_title

    # Applicable entities should include investment fund managers / AIFMs.
    joined = " ".join(d.applicable_entities).lower()
    assert (
        "alternative investment fund managers" in joined
        or "investment fund" in joined
        or "management compan" in joined
    )

    # PDF URL (English) should be present and end with .pdf.
    assert d.pdf_url_en is not None
    assert d.pdf_url_en.lower().endswith(".pdf")
    assert "eng" in d.pdf_url_en.lower()
    # French PDF too.
    assert d.pdf_url_fr is not None
    assert d.pdf_url_fr.lower().endswith(".pdf")

    # Published date.
    assert d.published_at == date(2022, 4, 22)
    # Updated date appears in the header.
    assert d.updated_at == date(2025, 4, 9)


def test_parse_detail_amends_refs_include_older_circulars() -> None:
    """The 22/806 detail page lists circulars it amends (CSSF 20/758, 04/155,
    IML 98/143, etc.) in the Related documents block."""
    html = (FIXTURES / "detail_22_806.html").read_text(encoding="utf-8")
    d = _parse_detail_html(html, source_url="https://example/")
    # Best-effort: at least one of these older circulars should be captured
    # as "amended by us".
    joined = " ".join(d.amends_refs)
    assert any(r in joined for r in ("CSSF 20/758", "CSSF 04/155", "IML 98/143"))


def test_list_circulars_uses_mock_transport_and_stops_on_empty_page() -> None:
    page1_html = (FIXTURES / "listing_aifms_page1.html").read_text(encoding="utf-8")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "/page/" not in str(request.url):
            return httpx.Response(200, text=page1_html)
        return httpx.Response(200, text="<html><body>no rows</body></html>")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://www.cssf.lu")

    rows = list(list_circulars("aifms", client=client, request_delay_ms=0))
    assert rows, "should yield at least one row"
    first_url = str(calls[0].url)
    assert "fwp_entity_type=aifms" in first_url
    assert "fwp_content_type=circulars-cssf" in first_url
    # Should have tried page 2 and stopped when it came back empty.
    assert any("/page/2/" in str(c.url) for c in calls)


def test_list_circulars_respects_max_pages() -> None:
    page1_html = (FIXTURES / "listing_aifms_page1.html").read_text(encoding="utf-8")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=page1_html)  # always returns rows

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://www.cssf.lu")

    rows = list(
        list_circulars("aifms", client=client, request_delay_ms=0, max_pages=1)
    )
    assert rows
    # Only one page should have been requested.
    assert len(calls) == 1


def test_fetch_circular_detail_raises_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://www.cssf.lu")
    with pytest.raises(CircularNotFoundError):
        fetch_circular_detail(
            "https://www.cssf.lu/en/Document/missing/",
            client=client,
            request_delay_ms=0,
        )


def test_fetch_circular_detail_parses_body() -> None:
    html = (FIXTURES / "detail_22_806.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://www.cssf.lu")
    d = fetch_circular_detail(
        "https://www.cssf.lu/en/Document/circular-cssf-22-806/",
        client=client,
        request_delay_ms=0,
    )
    assert d.reference_number == "CSSF 22/806"
    assert d.published_at == date(2022, 4, 22)
