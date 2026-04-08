from datetime import datetime, timezone
from pathlib import Path

from pytest_httpx import HTTPXMock

from regwatch.pipeline.fetch.ec_fisma_rss import EcFismaRssSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "ec_fisma_rss_sample.xml"


def test_fetch_combines_item_type_and_topic_feeds(httpx_mock: HTTPXMock) -> None:
    # Two feed URLs configured — one item_type, one topic. Both return the same
    # fixture. The source must dedupe by link.
    httpx_mock.add_response(
        url="https://ec.europa.eu/newsroom/fisma/feed?item_type_id=911&lang=en&orderby=item_date",
        content=FIXTURE.read_bytes(),
    )
    httpx_mock.add_response(
        url="https://ec.europa.eu/newsroom/fisma/feed?topic_id=1565&lang=en&orderby=item_date",
        content=FIXTURE.read_bytes(),
    )

    source = EcFismaRssSource(item_types=[911], topic_ids=[1565])
    items = list(source.fetch(datetime(2020, 1, 1, tzinfo=timezone.utc)))
    # 2 items per feed, deduped by link -> still 2.
    assert len(items) == 2
    assert items[0].source == "ec_fisma_rss"


def test_fetch_filters_by_since(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://ec.europa.eu/newsroom/fisma/feed?item_type_id=911&lang=en&orderby=item_date",
        content=FIXTURE.read_bytes(),
    )
    source = EcFismaRssSource(item_types=[911], topic_ids=[])
    items = list(source.fetch(datetime(2026, 4, 5, tzinfo=timezone.utc)))
    assert len(items) == 1
