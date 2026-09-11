"""Phase 4: persist the matched document into SQLite in a single transaction."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentVersion,
    Regulation,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.domain.types import ExtractedDocument, MatchedDocument, RawDocument
from regwatch.pipeline.diff import compute_diff
from regwatch.pipeline.hashing import content_hash, text_for_hashing


@dataclass
class PersistResult:
    event_id: int | None
    events_created: int
    versions_created: int
    version_ids: list[int] = field(default_factory=list)


def persist_matched(session: Session, matched: MatchedDocument) -> PersistResult:
    """Insert the matched document and all related rows. Idempotent by content hash."""
    extracted = matched.extracted
    raw = extracted.raw

    text_for_hash = text_for_hashing(extracted)
    document_hash = content_hash(text_for_hash)

    # Idempotency: skip if we already have an event with this content hash.
    existing = session.scalar(
        select(UpdateEvent).where(UpdateEvent.content_hash == document_hash)
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
        content_hash=document_hash,
        is_ict=matched.is_ict,
        severity=matched.severity,
        review_status="NEW",
        description=matched.description,
        applicable_entity_types=matched.applicable_entity_types,
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

    version_ids: list[int] = []
    for regulation_id in text_of(
        session, raw, [ref.regulation_id for ref in matched.references]
    ):
        version_id = _create_new_version(
            session, regulation_id, extracted, text_for_hash, document_hash
        )
        if version_id is not None:
            version_ids.append(version_id)

    return PersistResult(
        event_id=event.event_id,
        events_created=1,
        versions_created=len(version_ids),
        version_ids=version_ids,
    )


def text_of(
    session: Session, raw: RawDocument, regulation_ids: list[int]
) -> list[int]:
    """The matched regulations this document is a text *of*, not merely about.

    Only those get a new DocumentVersion; every match still links the event.
    A document is regulation R's text when its URL identifies R (R's own URL,
    CELEX id or ELI URI) or its title starts with R's reference ("Circular
    CSSF 22/806 ...", "Regulation (EU) 2022/2554 of ..."). A news item or FAQ
    that mentions R is not.
    """
    url = raw.source_url or ""
    title = re.sub(r"^\s*circular\s+", "", raw.title or "", flags=re.IGNORECASE)
    found: list[int] = []
    for reg in session.scalars(
        select(Regulation).where(Regulation.regulation_id.in_(regulation_ids))
    ):
        starts_title = re.match(
            re.escape(reg.reference_number) + r"(?![\d/])", title, flags=re.IGNORECASE
        )
        if (
            (reg.url and reg.url == url)
            or (reg.celex_id and reg.celex_id in url)
            or (reg.eli_uri and url.startswith(reg.eli_uri))
            or starts_title
        ):
            found.append(reg.regulation_id)
    return found


def _create_new_version(
    session: Session,
    regulation_id: int,
    extracted: ExtractedDocument,
    text: str,
    content_hash: str,
) -> int | None:
    """Insert a new document_version row if content has changed; return its id."""
    current = session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.regulation_id == regulation_id)
        .where(DocumentVersion.is_current == True)  # noqa: E712
    )
    if current is not None and current.content_hash == content_hash:
        return None

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
    return new_version.version_id
