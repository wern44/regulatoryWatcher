"""European Commission FISMA newsroom RSS source.

The FISMA newsroom exposes separate RSS feeds per `item_type_id` (publication
type) and per `topic_id`. We fetch all configured feeds and deduplicate by
entry link across the combined feed set.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from dateutil import parser as dateparser

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import USER_AGENT, register_source

_BASE_URL = "https://ec.europa.eu/newsroom/fisma/feed"


@register_source
class EcFismaRssSource:
    name = "ec_fisma_rss"

    def __init__(
        self, *, item_types: list[int], topic_ids: list[int]
    ) -> None:
        self._item_types = item_types
        self._topic_ids = topic_ids

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        seen_links: set[str] = set()
        now = datetime.now(UTC)
        with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            for url in self._feed_urls():
                response = client.get(url)
                response.raise_for_status()
                feed = feedparser.parse(response.content)
                for entry in feed.entries:
                    link = getattr(entry, "link", None)
                    if not link or link in seen_links:
                        continue
                    published_at = _parse_date(entry)
                    if published_at < since:
                        continue
                    seen_links.add(link)
                    yield RawDocument(
                        source=self.name,
                        source_url=link,
                        title=getattr(entry, "title", "").strip(),
                        published_at=published_at,
                        raw_payload={
                            "guid": getattr(entry, "id", None),
                            "description": getattr(entry, "description", None),
                            "feed_url": url,
                        },
                        fetched_at=now,
                    )

    def _feed_urls(self) -> Iterator[str]:
        for item_type in self._item_types:
            yield f"{_BASE_URL}?item_type_id={item_type}&lang=en&orderby=item_date"
        for topic_id in self._topic_ids:
            yield f"{_BASE_URL}?topic_id={topic_id}&lang=en&orderby=item_date"


def _parse_date(entry: Any) -> datetime:
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if raw is None:
        return datetime.now(UTC)
    parsed = dateparser.parse(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
