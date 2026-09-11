"""Chunk a DocumentVersion's text and write embeddings + FTS index rows."""
from __future__ import annotations

import logging
import struct
from collections.abc import Callable

from langdetect import detect
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from regwatch.db.models import DocumentChunk, DocumentVersion
from regwatch.llm.client import LLMClient
from regwatch.rag.chunker import chunk_text

logger = logging.getLogger(__name__)


def index_version(
    session: Session,
    version: DocumentVersion,
    *,
    ollama: LLMClient,
    chunk_size_tokens: int,
    overlap_tokens: int,
    authorization_types: list[str],
) -> int:
    """Chunk the given version and write chunk rows and vector rows.

    FTS rows are written by the ``document_chunk`` triggers
    (``regwatch/db/virtual_tables.py``). Returns the number of chunks created.
    """
    body = version.pdf_extracted_text or version.html_text or ""
    reg = version.regulation

    # Build a regulation metadata string for embedding enrichment.
    regulation_meta = (
        f"{reg.reference_number} — {reg.title} — {reg.issuing_authority}"
        if reg else ""
    )

    chunks = chunk_text(
        body,
        chunk_size_tokens=chunk_size_tokens,
        overlap_tokens=overlap_tokens,
        regulation_meta=regulation_meta,
    )
    if not chunks:
        return 0

    try:
        language = detect(body[:2000])
    except Exception:  # noqa: BLE001
        language = None

    chunk_rows: list[DocumentChunk] = []
    for c in chunks:
        row = DocumentChunk(
            version_id=version.version_id,
            regulation_id=version.regulation_id,
            chunk_index=c.index,
            text=c.text,
            token_count=c.token_count,
            language=language,
            lifecycle_stage=reg.lifecycle_stage.value,
            is_ict=reg.is_ict,
            authorization_types=authorization_types,
            heading_path=c.heading_path,
            cross_refs=c.cross_refs or None,
            is_definition=c.is_definition,
        )
        session.add(row)
        chunk_rows.append(row)

    session.flush()

    for row, c in zip(chunk_rows, chunks, strict=True):
        vector = ollama.embed(c.embed_text)
        packed = _pack_f32(vector)
        session.execute(
            sa_text(
                "INSERT INTO document_chunk_vec(chunk_id, embedding) VALUES (:id, :vec)"
            ),
            {"id": row.chunk_id, "vec": packed},
        )

    return len(chunk_rows)


def index_pending_versions(
    session: Session,
    *,
    ollama: LLMClient,
    chunk_size_tokens: int,
    overlap_tokens: int,
    authorization_types: list[str],
    should_stop: Callable[[], bool] = lambda: False,
) -> int:
    """Index every document version that has text but no chunks yet.

    Commits after each version. Stops at the first failure (typically the
    embedding model being unavailable); the next call picks up where this one
    stopped. Returns the number of versions indexed.
    """
    indexed_ids = select(DocumentChunk.version_id)
    pending = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.version_id.not_in(indexed_ids))
        .order_by(DocumentVersion.version_id)
    ).all()
    done = 0
    for version in pending:
        if should_stop():
            break
        if not (version.pdf_extracted_text or version.html_text):
            continue
        try:
            index_version(
                session, version, ollama=ollama,
                chunk_size_tokens=chunk_size_tokens,
                overlap_tokens=overlap_tokens,
                authorization_types=authorization_types,
            )
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception(
                "Indexing version %s failed; leaving the rest for the next run",
                version.version_id,
            )
            break
        done += 1
    return done


def _pack_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)
