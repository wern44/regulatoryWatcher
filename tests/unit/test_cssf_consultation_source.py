from datetime import datetime, timezone
from pathlib import Path

from pytest_httpx import HTTPXMock

from regwatch.pipeline.fetch.cssf_consultation import CssfConsultationSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cssf_consultation_sample.xml"


def test_fetch_filters_to_consultation_titles_only(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications",
        content=FIXTURE.read_bytes(),
        headers={"content-type": "application/rss+xml"},
    )
    source = CssfConsultationSource()
    items = list(source.fetch(datetime(2020, 1, 1, tzinfo=timezone.utc)))

    # 3 of 4 items should match (consultation / discussion paper / feedback).
    assert len(items) == 3
    titles = [i.title for i in items]
    assert all(
        any(
            kw in t.lower()
            for kw in ("consultation", "discussion paper", "feedback")
        )
        for t in titles
    )
    # The plain circular must NOT be included.
    assert not any("25/901" in t for t in titles)


def test_fetch_filters_by_since(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications",
        content=FIXTURE.read_bytes(),
    )
    source = CssfConsultationSource()
    items = list(source.fetch(datetime(2026, 4, 5, tzinfo=timezone.utc)))
    assert len(items) == 1
    assert "AIFMD" in items[0].title
