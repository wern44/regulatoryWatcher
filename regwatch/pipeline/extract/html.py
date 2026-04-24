"""HTML text extraction using trafilatura."""
from __future__ import annotations

import logging
import time

import httpx
import trafilatura

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import USER_AGENT

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0
_MAX_RETRIES = 3
_RETRY_BACKOFF = (2, 5, 10)  # seconds between retries on 429


def extract_html(raw: RawDocument) -> str | None:
    """Download the source URL, extract main text. Returns None for PDF URLs."""
    url = raw.source_url
    if url.lower().endswith(".pdf"):
        return None

    with httpx.Client(
        timeout=_HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = _get_with_retry(client, url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "pdf" in content_type.lower():
            return None
        html = response.text

    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    return text


def _get_with_retry(client: httpx.Client, url: str) -> httpx.Response:
    """GET with retry on 429 Too Many Requests."""
    for attempt in range(_MAX_RETRIES):
        response = client.get(url)
        if response.status_code != 429:
            return response
        wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
        logger.warning(
            "429 Too Many Requests for %s — retrying in %ds (attempt %d/%d)",
            url, wait, attempt + 1, _MAX_RETRIES,
        )
        time.sleep(wait)
    return response  # return last 429 response — caller's raise_for_status will handle it
