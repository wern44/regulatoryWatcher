"""Hybrid retrieval: dense sqlite-vec + sparse FTS5 merged by reciprocal rank fusion.

The dense/sparse queries are deliberately run without WHERE-filter binds to
avoid SQLAlchemy expanding-bindparam edge cases with sqlite-vec. Filtering
happens in Python during hydration, which is acceptable for the expected
corpus sizes (tens of thousands of chunks).
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from regwatch.db.models import DocumentChunk
from regwatch.llm.client import LLMClient


@dataclass
class RetrievalFilters:
    is_ict: bool | None = None
    authorization_type: str | None = None  # "AIFM" or "CHAPTER15_MANCO"
    lifecycle_stages: list[str] = field(default_factory=list)
    regulation_ids: list[int] = field(default_factory=list)


@dataclass
class RetrievedChunk:
    chunk_id: int
    version_id: int
    regulation_id: int
    text: str
    is_ict: bool
    lifecycle_stage: str
    score: float


class HybridRetriever:
    def __init__(
        self, session: Session, *, ollama: LLMClient, top_k: int = 20
    ) -> None:
        self._session = session
        self._ollama = ollama
        self._top_k = top_k

    def retrieve(
        self, query: str, filters: RetrievalFilters
    ) -> list[RetrievedChunk]:
        query_vec = self._ollama.embed(query)
        # Pull a larger candidate pool from each retriever, then fuse and filter
        # client-side during hydration.
        pool = max(self._top_k * 3, 30)
        dense_hits = self._dense_search(query_vec, k=pool)
        sparse_hits = self._sparse_search(query, k=pool)
        fused_ids = _reciprocal_rank_fusion(dense_hits, sparse_hits, k=60)
        return self._hydrate(fused_ids, filters)[: self._top_k]

    def _dense_search(self, vec: list[float], *, k: int) -> list[int]:
        packed = struct.pack(f"{len(vec)}f", *vec)
        rows = self._session.execute(
            sa_text(
                """
                SELECT chunk_id
                FROM document_chunk_vec
                WHERE embedding MATCH :vec AND k = :k
                ORDER BY distance
                """
            ),
            {"vec": packed, "k": k},
        ).all()
        return [r[0] for r in rows]

    def _sparse_search(self, query: str, *, k: int) -> list[int]:
        safe_query = _sanitize_fts_query(query)
        if not safe_query:
            return []
        rows = self._session.execute(
            sa_text(
                """
                SELECT rowid
                FROM document_chunk_fts
                WHERE document_chunk_fts MATCH :q
                ORDER BY bm25(document_chunk_fts)
                LIMIT :k
                """
            ),
            {"q": safe_query, "k": k},
        ).all()
        return [r[0] for r in rows]

    def _hydrate(
        self, chunk_ids: list[int], filters: RetrievalFilters
    ) -> list[RetrievedChunk]:
        if not chunk_ids:
            return []
        rows = (
            self._session.query(DocumentChunk)
            .filter(DocumentChunk.chunk_id.in_(chunk_ids))
            .all()
        )
        by_id = {r.chunk_id: r for r in rows}
        out: list[RetrievedChunk] = []
        for i, cid in enumerate(chunk_ids):
            r = by_id.get(cid)
            if r is None:
                continue
            if filters.is_ict is not None and r.is_ict != filters.is_ict:
                continue
            if (
                filters.lifecycle_stages
                and r.lifecycle_stage not in filters.lifecycle_stages
            ):
                continue
            if (
                filters.regulation_ids
                and r.regulation_id not in filters.regulation_ids
            ):
                continue
            if filters.authorization_type is not None and (
                not r.authorization_types
                or filters.authorization_type not in r.authorization_types
            ):
                continue
            out.append(
                RetrievedChunk(
                    chunk_id=r.chunk_id,
                    version_id=r.version_id,
                    regulation_id=r.regulation_id,
                    text=r.text,
                    is_ict=r.is_ict,
                    lifecycle_stage=r.lifecycle_stage,
                    score=1.0 / (i + 1),
                )
            )
        return out


_FTS_SPECIAL = re.compile(r"[^\w\s]", re.UNICODE)


def _sanitize_fts_query(query: str) -> str:
    """Strip FTS5 special characters and wrap bare terms for a safe OR query."""
    cleaned = _FTS_SPECIAL.sub(" ", query).strip()
    if not cleaned:
        return ""
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return ""
    # Use OR to be forgiving: FTS5 defaults to AND and would miss most results
    # for natural-language questions.
    return " OR ".join(f'"{t}"' for t in tokens)


def _reciprocal_rank_fusion(
    dense: list[int], sparse: list[int], *, k: int = 60
) -> list[int]:
    scores: dict[int, float] = {}
    for rank, cid in enumerate(dense):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    for rank, cid in enumerate(sparse):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return [
        cid
        for cid, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ]
