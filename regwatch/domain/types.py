"""Pipeline domain dataclasses passed between phases."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawDocument:
    """Raw item as returned by a Source plugin. Text content is NOT loaded yet."""

    source: str
    source_url: str
    title: str
    published_at: datetime
    raw_payload: dict[str, Any]
    fetched_at: datetime


@dataclass
class ExtractedDocument:
    """A RawDocument plus its extracted text content (HTML and/or PDF)."""

    raw: RawDocument
    html_text: str | None
    pdf_path: str | None
    pdf_extracted_text: str | None
    pdf_is_protected: bool


@dataclass
class MatchedReference:
    """One match between a document and a catalog regulation."""

    regulation_id: int
    method: str  # REGEX_ALIAS / CELEX_ID / ELI_URI / OLLAMA_REFERENCE / OLLAMA_SEMANTIC / MANUAL
    confidence: float
    snippet: str | None = None


@dataclass
class MatchedDocument:
    """An ExtractedDocument plus its matched regulations and classifications."""

    extracted: ExtractedDocument
    references: list[MatchedReference] = field(default_factory=list)
    lifecycle_stage: str = "IN_FORCE"
    is_ict: bool = False
    severity: str = "INFORMATIONAL"
