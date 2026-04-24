"""EBA news RSS source."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from dateutil import parser as dateparser

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import USER_AGENT, register_source


@register_source
class EbaRssSource:
    name = "eba_rss"
    url = "https://www.eba.europa.eu/news-press/news/rss.xml"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        now = datetime.now(UTC)
        with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(self.url)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        for entry in feed.entries:
            link = getattr(entry, "link", None)
            if not link:
                continue
            published_at = _parse_date(entry)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=link,
                title=getattr(entry, "title", "").strip(),
                published_at=published_at,
                raw_payload={
                    "guid": getattr(entry, "id", None),
                    "description": getattr(entry, "description", None),
                },
                fetched_at=now,
            )


def _parse_date(entry: Any) -> datetime:
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if raw is None:
        return datetime.now(UTC)
    parsed = dateparser.parse(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
