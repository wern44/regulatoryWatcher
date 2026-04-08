"""CSSF RSS source plugin: one feed per keyword, deduped by link."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from dateutil import parser as dateparser

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source


@register_source
class CssfRssSource:
    name = "cssf_rss"
    base_url = "https://www.cssf.lu/en/feed/publications"

    def __init__(self, keywords: list[str]) -> None:
        self.keywords = keywords
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        seen_links: set[str] = set()
        now = datetime.now(timezone.utc)
        for keyword in self.keywords:
            url = f"{self.base_url}?content_keyword={keyword}"
            response = self._client.get(url)
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
                    raw_payload=_entry_to_dict(entry, keyword),
                    fetched_at=now,
                )


def _parse_date(entry: Any) -> datetime:
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if raw is None:
        return datetime.now(timezone.utc)
    parsed = dateparser.parse(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _entry_to_dict(entry: Any, keyword: str) -> dict[str, Any]:
    return {
        "guid": getattr(entry, "id", None) or getattr(entry, "guid", None),
        "description": getattr(entry, "description", None),
        "keyword": keyword,
    }
