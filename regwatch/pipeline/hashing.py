"""Pure helpers for computing the content hash used to dedupe documents.

Lives in its own module so the runner (pre-match short-circuit) and
persist.py (idempotency safety net) agree on the formula.
"""
from __future__ import annotations

import hashlib

from regwatch.domain.types import ExtractedDocument


def text_for_hashing(extracted: ExtractedDocument) -> str:
    """Return the text we hash to identify a document.

    Prefers the PDF-extracted text over the HTML body when both are
    present. Whitespace is stripped so trivial trailing-newline
    differences do not produce different hashes.
    """
    return (extracted.pdf_extracted_text or extracted.html_text or "").strip()


def content_hash(text: str) -> str:
    """Return the lowercase hex SHA-256 of *text* (UTF-8 encoded)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
