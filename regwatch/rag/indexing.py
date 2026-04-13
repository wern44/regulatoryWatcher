"""Chunk a DocumentVersion's text and write embeddings + FTS index rows."""
from __future__ import annotations

import struct

from langdetect import detect
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from regwatch.db.models import DocumentChunk, DocumentVersion
from regwatch.llm.client import LLMClient
from regwatch.rag.chunker import chunk_text


def index_version(
    session: Session,
    version: DocumentVersion,
    *,
    ollama: LLMClient,
    chunk_size_tokens: int,
    overlap_tokens: int,
    authorization_types: list[str],
) -> int:
    """Chunk the given version and write chunk rows, vector rows, and FTS rows.

    Returns the number of chunks created.
    """
    body = version.pdf_extracted_text or version.html_text or ""
    chunks = chunk_text(
        body,
        chunk_size_tokens=chunk_size_tokens,
        overlap_tokens=overlap_tokens,
    )
    if not chunks:
        return 0

    try:
        language = detect(body[:2000])
    except Exception:  # noqa: BLE001
        language = None

    reg = version.regulation

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
        )
        session.add(row)
        chunk_rows.append(row)

    session.flush()

    for row, c in zip(chunk_rows, chunks, strict=True):
        vector = ollama.embed(c.text)
        packed = _pack_f32(vector)
        session.execute(
            sa_text(
                "INSERT INTO document_chunk_vec(chunk_id, embedding) VALUES (:id, :vec)"
            ),
            {"id": row.chunk_id, "vec": packed},
        )
        session.execute(
            sa_text(
                "INSERT INTO document_chunk_fts(rowid, text) VALUES (:id, :text)"
            ),
            {"id": row.chunk_id, "text": c.text},
        )

    return len(chunk_rows)


def _pack_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)
