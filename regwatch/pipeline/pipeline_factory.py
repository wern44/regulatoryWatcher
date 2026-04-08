"""Factory that wires sources, extract and match functions into a PipelineRunner.

For tests that do not have network or real URLs, the raw_payload may carry a
pre-extracted `html_text` key. The factory's extract function honours that first.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.domain.types import ExtractedDocument, MatchedDocument, RawDocument
from regwatch.ollama.client import OllamaClient
from regwatch.pipeline.extract.html import extract_html
from regwatch.pipeline.extract.pdf import extract_pdf
from regwatch.pipeline.match.classify import is_ict_document, severity_for
from regwatch.pipeline.match.combined import CombinedMatcher
from regwatch.pipeline.match.lifecycle import classify_lifecycle
from regwatch.pipeline.runner import PipelineRunner


def build_runner(
    session: Session,
    *,
    sources: Iterable,
    archive_root: Path | str,
    ollama_client: OllamaClient | None = None,
) -> PipelineRunner:
    combined = CombinedMatcher(session, ollama=ollama_client)

    def _extract(raw: RawDocument) -> ExtractedDocument:
        # Test hook: prefer in-memory text over real HTTP.
        prefetched = raw.raw_payload.get("html_text") if raw.raw_payload else None
        if prefetched:
            return ExtractedDocument(
                raw=raw,
                html_text=prefetched,
                pdf_path=None,
                pdf_extracted_text=None,
                pdf_is_protected=False,
            )
        if raw.source_url.lower().endswith(".pdf"):
            result = extract_pdf(raw, archive_root)
            return ExtractedDocument(
                raw=raw,
                html_text=None,
                pdf_path=result.archive_path,
                pdf_extracted_text=result.text,
                pdf_is_protected=result.is_protected,
            )
        text = extract_html(raw)
        return ExtractedDocument(
            raw=raw,
            html_text=text,
            pdf_path=None,
            pdf_extracted_text=None,
            pdf_is_protected=False,
        )

    def _match(extracted: ExtractedDocument) -> MatchedDocument:
        text_for_match = (
            extracted.pdf_extracted_text
            or extracted.html_text
            or extracted.raw.title
            or ""
        )
        references = combined.match(text_for_match)
        is_ict = is_ict_document(
            extracted.raw.title + " " + (text_for_match or "")
        )
        lifecycle = classify_lifecycle(
            title=extracted.raw.title,
            celex_id=None,
            url=extracted.raw.source_url,
            application_date=None,
            today=date.today(),
        )
        severity = severity_for(
            title=extracted.raw.title,
            is_ict=is_ict,
            references_in_force=bool(references),
        )
        return MatchedDocument(
            extracted=extracted,
            references=references,
            lifecycle_stage=lifecycle,
            is_ict=is_ict,
            severity=severity,
        )

    return PipelineRunner(
        session, sources=sources, extract=_extract, match=_match
    )
