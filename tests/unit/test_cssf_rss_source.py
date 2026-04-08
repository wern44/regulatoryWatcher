from datetime import datetime, timezone
from pathlib import Path

from pytest_httpx import HTTPXMock

from regwatch.pipeline.fetch.cssf_rss import CssfRssSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cssf_rss_sample.xml"


def test_fetch_parses_items(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications?content_keyword=aif",
        content=FIXTURE.read_bytes(),
        headers={"content-type": "application/rss+xml"},
    )

    source = CssfRssSource(keywords=["aif"])
    since = datetime(2020, 1, 1, tzinfo=timezone.utc)
    items = list(source.fetch(since))

    assert len(items) == 2
    assert items[0].source == "cssf_rss"
    assert items[0].title == "Circular CSSF 25/901 on ICT outsourcing notifications"
    assert items[0].source_url == "https://www.cssf.lu/en/Document/circular-cssf-25-901/"
    assert items[0].published_at.tzinfo is not None


def test_fetch_filters_by_since(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications?content_keyword=aif",
        content=FIXTURE.read_bytes(),
        headers={"content-type": "application/rss+xml"},
    )
    source = CssfRssSource(keywords=["aif"])
    since = datetime(2026, 4, 5, tzinfo=timezone.utc)
    items = list(source.fetch(since))
    assert len(items) == 1
    assert "25/901" in items[0].title


def test_fetch_combines_multiple_keywords(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications?content_keyword=aif",
        content=FIXTURE.read_bytes(),
    )
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications?content_keyword=ucits",
        content=FIXTURE.read_bytes(),
    )
    source = CssfRssSource(keywords=["aif", "ucits"])
    items = list(source.fetch(datetime(2020, 1, 1, tzinfo=timezone.utc)))
    # Items are deduplicated by link, so 2 items despite 2 feeds.
    assert len(items) == 2
