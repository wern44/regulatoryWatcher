"""HTML text extraction using trafilatura."""
from __future__ import annotations

import httpx
import trafilatura

from regwatch.domain.types import RawDocument

_HTTP_TIMEOUT = 30.0


def extract_html(raw: RawDocument) -> str | None:
    """Download the source URL, extract main text. Returns None for PDF URLs."""
    url = raw.source_url
    if url.lower().endswith(".pdf"):
        return None

    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "pdf" in content_type.lower():
            return None
        html = response.text

    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    return text
