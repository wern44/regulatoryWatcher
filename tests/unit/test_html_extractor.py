from datetime import datetime, timezone
from pathlib import Path

from pytest_httpx import HTTPXMock

from regwatch.domain.types import RawDocument
from regwatch.pipeline.extract.html import extract_html

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cssf_circular_page.html"


def _raw(url: str) -> RawDocument:
    now = datetime.now(timezone.utc)
    return RawDocument(
        source="cssf_rss",
        source_url=url,
        title="Circular CSSF 18/698",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )


def test_extract_html_strips_boilerplate(httpx_mock: HTTPXMock) -> None:
    url = "https://www.cssf.lu/en/Document/circular-cssf-18-698/"
    httpx_mock.add_response(
        url=url,
        content=FIXTURE.read_bytes(),
        headers={"content-type": "text/html"},
    )

    text = extract_html(_raw(url))

    assert text is not None
    assert "Investment Fund Managers" in text
    assert "navigation should be stripped" not in text
    assert "footer should be stripped" not in text


def test_extract_html_returns_none_for_pdf_link(httpx_mock: HTTPXMock) -> None:
    url = "https://www.cssf.lu/wp-content/uploads/cssf-25-901.pdf"
    # Don't register any mock — function should short-circuit on .pdf suffix.
    text = extract_html(_raw(url))
    assert text is None
