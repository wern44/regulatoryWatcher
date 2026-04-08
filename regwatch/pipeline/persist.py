"""Phase 4: persist the matched document into SQLite in a single transaction."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentVersion,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.domain.types import ExtractedDocument, MatchedDocument
from regwatch.pipeline.diff import compute_diff


@dataclass
class PersistResult:
    event_id: int | None
    events_created: int
    versions_created: int


def persist_matched(session: Session, matched: MatchedDocument) -> PersistResult:
    """Insert the matched document and all related rows. Idempotent by content hash."""
    extracted = matched.extracted
    raw = extracted.raw

    text_for_hash = _text_for_hashing(extracted)
    content_hash = hashlib.sha256(text_for_hash.encode("utf-8")).hexdigest()

    # Idempotency: skip if we already have an event with this content hash.
    existing = session.scalar(
        select(UpdateEvent).where(UpdateEvent.content_hash == content_hash)
    )
    if existing is not None:
        return PersistResult(
            event_id=existing.event_id, events_created=0, versions_created=0
        )

    event = UpdateEvent(
        source=raw.source,
        source_url=raw.source_url,
        title=raw.title,
        published_at=raw.published_at,
        fetched_at=raw.fetched_at,
        raw_payload=raw.raw_payload,
        content_hash=content_hash,
        is_ict=matched.is_ict,
        severity=matched.severity,
        review_status="NEW",
    )
    for ref in matched.references:
        event.regulation_links.append(
            UpdateEventRegulationLink(
                regulation_id=ref.regulation_id,
                match_method=ref.method,
                confidence=ref.confidence,
                matched_snippet=ref.snippet,
            )
        )
    session.add(event)
    session.flush()

    versions_created = 0
    for ref in matched.references:
        if _create_new_version(
            session, ref.regulation_id, extracted, text_for_hash, content_hash
        ):
            versions_created += 1

    return PersistResult(
        event_id=event.event_id, events_created=1, versions_created=versions_created
    )


def _text_for_hashing(extracted: ExtractedDocument) -> str:
    return (extracted.pdf_extracted_text or extracted.html_text or "").strip()


def _create_new_version(
    session: Session,
    regulation_id: int,
    extracted: ExtractedDocument,
    text: str,
    content_hash: str,
) -> bool:
    """Insert a new document_version row if content has changed. Returns True if inserted."""
    current = session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.regulation_id == regulation_id)
        .where(DocumentVersion.is_current == True)  # noqa: E712
    )
    if current is not None and current.content_hash == content_hash:
        return False

    prev_text = ""
    prev_number = 0
    if current is not None:
        prev_text = current.pdf_extracted_text or current.html_text or ""
        prev_number = current.version_number
        session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.version_id == current.version_id)
            .values(is_current=False)
        )

    change_summary = compute_diff(prev_text, text) if prev_text else None

    new_version = DocumentVersion(
        regulation_id=regulation_id,
        version_number=prev_number + 1,
        is_current=True,
        fetched_at=datetime.now(UTC),
        source_url=extracted.raw.source_url,
        content_hash=content_hash,
        html_text=extracted.html_text,
        pdf_path=extracted.pdf_path,
        pdf_extracted_text=extracted.pdf_extracted_text,
        pdf_is_protected=extracted.pdf_is_protected,
        pdf_manual_upload=False,
        change_summary=change_summary,
    )
    session.add(new_version)
    session.flush()
    return True
