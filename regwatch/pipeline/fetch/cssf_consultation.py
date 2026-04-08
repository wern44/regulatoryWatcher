"""CSSF consultation source: filters the main CSSF feed for consultation items.

CSSF does not publish a dedicated consultation feed. We pull the base
publications feed and filter client-side on title / description for
`consultation`, `discussion paper`, or `feedback` keywords.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from dateutil import parser as dateparser

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source

_BASE_URL = "https://www.cssf.lu/en/feed/publications"
_CONSULTATION_KEYWORDS = (
    "consultation",
    "discussion paper",
    "feedback",
)


@register_source
class CssfConsultationSource:
    name = "cssf_consultation"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        now = datetime.now(timezone.utc)
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(_BASE_URL)
            response.raise_for_status()
            feed = feedparser.parse(response.content)

        for entry in feed.entries:
            link = getattr(entry, "link", None)
            if not link:
                continue
            title = getattr(entry, "title", "").strip()
            description = getattr(entry, "description", "") or ""
            haystack = f"{title} {description}".lower()
            if not any(kw in haystack for kw in _CONSULTATION_KEYWORDS):
                continue
            published_at = _parse_date(entry)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=link,
                title=title,
                published_at=published_at,
                raw_payload={
                    "guid": getattr(entry, "id", None),
                    "description": description,
                },
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
