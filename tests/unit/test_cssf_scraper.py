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
    _parse_listing_page,
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

    rows = list(
        list_circulars(
            entity_filter_id=502,
            content_type_filter_id=567,
            publication_type_label="CSSF circular",
            client=client,
            request_delay_ms=0,
        )
    )
    assert rows, "should yield at least one row"
    first_url = str(calls[0].url)
    assert "entity_type=502" in first_url
    assert "content_type=567" in first_url
    # Should have tried page 2 and stopped when it came back empty.
    assert any("/page/2/" in str(c.url) for c in calls)


def test_list_circulars_skips_pages_with_no_cssf_matches() -> None:
    """Pagination must NOT terminate when a page has items but none match _REF_RE.

    Only an empty ``<li.library-element>`` list (raw_count=0) ends the walk.
    The real CSSF listing interleaves EU regulations (rejected by ``_REF_RE``)
    with CSSF circulars, so a page of 20 non-CSSF rows is expected and the
    walker must continue past it.
    """
    cssf_row = '''
    <li class="library-element">
      <div class="library-element__title">
        <a href="/en/Document/circular-cssf-25-893/">Circular CSSF 25/893</a>
      </div>
      <div class="library-element__subtitle">Scope text</div>
      <div class="date--published">Published on 10.10.2025</div>
    </li>
    '''
    non_cssf_row = '''
    <li class="library-element">
      <div class="library-element__title">
        <a href="/en/Document/foo/">Council Implementing Regulation (EU) 2025/1476</a>
      </div>
      <div class="library-element__subtitle">Scope</div>
      <div class="date--published">Published on 18.07.2025</div>
    </li>
    '''

    page1 = f"<html><body><ul>{cssf_row}</ul></body></html>"
    page2 = f"<html><body><ul>{non_cssf_row * 5}</ul></body></html>"  # 5 non-CSSF
    page3 = (
        "<html><body><ul>"
        f"{cssf_row.replace('25/893', '25/880').replace('cssf-25-893', 'cssf-25-880')}"
        "</ul></body></html>"
    )
    page4 = "<html><body><ul></ul></body></html>"  # no library-element items

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/page/2/" in path:
            return httpx.Response(200, text=page2)
        if "/page/3/" in path:
            return httpx.Response(200, text=page3)
        if "/page/4/" in path:
            return httpx.Response(200, text=page4)
        if path.rstrip("/").endswith("/regulatory-framework"):
            return httpx.Response(200, text=page1)
        return httpx.Response(200, text=page4)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://www.cssf.lu")

    rows = list(
        list_circulars(
            entity_filter_id=502,
            content_type_filter_id=567,
            publication_type_label="CSSF circular",
            client=client,
            request_delay_ms=0,
        )
    )
    refs = [r.reference_number for r in rows]
    assert "CSSF 25/893" in refs, (
        f"expected page 1 CSSF row to yield; got {refs}"
    )
    assert "CSSF 25/880" in refs, (
        "expected page 3 CSSF row to yield "
        f"(page 2's non-CSSF content must NOT stop pagination); got {refs}"
    )
    assert len(rows) == 2


def test_list_circulars_respects_max_pages() -> None:
    page1_html = (FIXTURES / "listing_aifms_page1.html").read_text(encoding="utf-8")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=page1_html)  # always returns rows

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://www.cssf.lu")

    rows = list(
        list_circulars(
            entity_filter_id=502,
            content_type_filter_id=567,
            publication_type_label="CSSF circular",
            client=client,
            request_delay_ms=0,
            max_pages=1,
        )
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


def test_parse_detail_extracts_subtitle_description() -> None:
    html = (FIXTURES / "detail_22_806.html").read_text(encoding="utf-8")
    d = _parse_detail_html(
        html, source_url="https://example/circular-cssf-22-806/"
    )
    assert d.description == "on outsourcing arrangements", (
        f"got: {d.description!r}"
    )


def test_compose_title_prefers_informative_clean_title() -> None:
    from datetime import date

    from regwatch.discovery.cssf_scraper import (
        CircularDetail,
        CircularListingRow,
    )
    from regwatch.services.cssf_discovery import _compose_title

    detail = CircularDetail(
        reference_number="CSSF 22/806",
        clean_title="Circular CSSF 22/806 on outsourcing arrangements",
        description="on outsourcing arrangements",
        published_at=date(2022, 4, 22),
    )
    listing = CircularListingRow(
        reference_number="CSSF 22/806",
        raw_title="x",
        description="y",
        publication_date=date(2022, 4, 22),
        detail_url="u",
    )
    assert _compose_title(detail, listing) == (
        "Circular CSSF 22/806 on outsourcing arrangements"
    )


def test_compose_title_combines_ref_and_subtitle_when_clean_title_is_bare() -> None:
    from datetime import date

    from regwatch.discovery.cssf_scraper import (
        CircularDetail,
        CircularListingRow,
    )
    from regwatch.services.cssf_discovery import _compose_title

    detail = CircularDetail(
        reference_number="CSSF 25/896",
        clean_title="Circular CSSF 25/896",  # bare -- just the ref
        description="on residential real estate reporting",
        published_at=date(2025, 5, 1),
    )
    listing = CircularListingRow(
        reference_number="CSSF 25/896",
        raw_title="",
        description="",
        publication_date=date(2025, 5, 1),
        detail_url="u",
    )
    assert _compose_title(detail, listing) == (
        "Circular CSSF 25/896 on residential real estate reporting"
    )


def test_compose_title_handles_missing_reference_number() -> None:
    from datetime import date

    from regwatch.discovery.cssf_scraper import (
        CircularDetail,
        CircularListingRow,
    )
    from regwatch.services.cssf_discovery import _compose_title

    detail = CircularDetail(
        reference_number="",
        clean_title="",
        description="",
        published_at=date(2025, 1, 1),
    )
    listing = CircularListingRow(
        reference_number="",
        raw_title="Some fallback title",
        description="",
        publication_date=date(2025, 1, 1),
        detail_url="u",
    )
    # No ref -> should return the listing title without any "Circular " prefix.
    assert _compose_title(detail, listing) == "Some fallback title"


def test_slug_from_reference() -> None:
    from regwatch.services.cssf_discovery import _slug_from_reference

    assert _slug_from_reference("CSSF 22/806") == "circular-cssf-22-806"
    assert (
        _slug_from_reference("CSSF-CPDI 26/50") == "circular-cssf-cpdi-26-50"
    )
    assert _slug_from_reference("garbage") is None
    assert _slug_from_reference("") is None


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


# ---------------------------------------------------------------------------
# New tests for Task 7 — numeric URL params + synthetic refs
# ---------------------------------------------------------------------------


def test_law_listing_synthesizes_reference_from_slug() -> None:
    html = (FIXTURES / "listing_aifms_law.html").read_text(encoding="utf-8")
    rows, raw_count = _parse_listing_page(html, publication_type_label="Law")
    assert raw_count > 0
    assert rows, "expected at least one Law row parsed"
    for r in rows:
        assert r.publication_type_label == "Law"
        assert r.reference_number, "must have synthesized ref"
        # Should be URL-slug-derived; starts with 'law-' prefix
        assert r.reference_number.startswith("law-"), (
            f"expected law- prefix, got {r.reference_number!r}"
        )


def test_grand_ducal_listing_synthesizes_reference_from_slug() -> None:
    html = (FIXTURES / "listing_aifms_grand-ducal-regulation.html").read_text(
        encoding="utf-8"
    )
    rows, raw_count = _parse_listing_page(
        html, publication_type_label="Grand-ducal regulation"
    )
    assert raw_count > 0
    assert rows
    for r in rows:
        assert r.publication_type_label == "Grand-ducal regulation"
        assert r.reference_number
        assert r.reference_number.startswith("grand-ducal-"), (
            f"expected grand-ducal- prefix, got {r.reference_number!r}"
        )


def test_cssf_circular_listing_still_uses_ref_regex() -> None:
    html = (FIXTURES / "listing_aifms_page1.html").read_text(encoding="utf-8")
    rows, _ = _parse_listing_page(html, publication_type_label="CSSF circular")
    assert rows
    for r in rows:
        assert r.publication_type_label == "CSSF circular"
        # Canonical CSSF/IML ref shape enforced
        assert re.match(
            r"^(CSSF(-[A-Z]+)?|IML|BCL)\s\d{2,4}/\d{1,4}$",
            r.reference_number,
        ), f"not a canonical ref: {r.reference_number!r}"


def test_list_circulars_uses_numeric_url_params(httpx_mock) -> None:
    """list_circulars must send entity_type and content_type as numeric IDs."""
    # httpx_mock will match any GET; we verify request URL.
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/regulatory-framework/?entity_type=502&content_type=567",
        text="<html><body></body></html>",
    )
    # First page returns empty -> pagination stops immediately.
    with httpx.Client() as client:
        rows = list(
            list_circulars(
                entity_filter_id=502,
                content_type_filter_id=567,
                publication_type_label="CSSF circular",
                client=client,
                request_delay_ms=0,
            )
        )
    assert rows == []
    req = httpx_mock.get_requests()[0]
    # Assert URL has numeric params (not slug-based fwp_*)
    assert "entity_type=502" in str(req.url)
    assert "content_type=567" in str(req.url)
    assert "fwp_" not in str(req.url), f"legacy fwp_* param leaked: {req.url}"
