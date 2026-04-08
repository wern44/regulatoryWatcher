from datetime import datetime, timezone
from pathlib import Path

from pytest_httpx import HTTPXMock

from regwatch.pipeline.fetch.esma_rss import EsmaRssSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "esma_rss_sample.xml"


def test_fetch_parses_esma_items(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.esma.europa.eu/rss.xml",
        content=FIXTURE.read_bytes(),
        headers={"content-type": "application/rss+xml"},
    )

    source = EsmaRssSource()
    items = list(source.fetch(datetime(2020, 1, 1, tzinfo=timezone.utc)))

    assert len(items) == 2
    assert items[0].source == "esma_rss"
    assert "liquidity" in items[0].title.lower()


def test_fetch_filters_by_since(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.esma.europa.eu/rss.xml",
        content=FIXTURE.read_bytes(),
    )
    source = EsmaRssSource()
    items = list(source.fetch(datetime(2026, 4, 5, tzinfo=timezone.utc)))
    assert len(items) == 1
