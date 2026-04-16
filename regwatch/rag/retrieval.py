"""Hybrid retrieval: dense sqlite-vec + sparse FTS5 merged by reciprocal rank fusion.

The dense/sparse queries are deliberately run without WHERE-filter binds to
avoid SQLAlchemy expanding-bindparam edge cases with sqlite-vec. Filtering
happens in Python during hydration, which is acceptable for the expected
corpus sizes (tens of thousands of chunks).

Small-to-big expansion: when a chunk's heading_path indicates it's part of a
larger article, sibling chunks from the same article are pulled in to give the
LLM full article context.  Definition chunks referenced via cross_refs are also
included automatically.
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
    version_ids: list[int] = field(default_factory=list)


@dataclass
class RetrievedChunk:
    chunk_id: int
    version_id: int
    regulation_id: int
    text: str
    is_ict: bool
    lifecycle_stage: str
    score: float
    heading_path: list[str] = field(default_factory=list)
    is_expansion: bool = False  # True for sibling/definition chunks added by expansion


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
        pool = max(self._top_k * (6 if filters.version_ids else 3), 30)
        dense_hits = self._dense_search(query_vec, k=pool)
        sparse_hits = self._sparse_search(query, k=pool)
        fused_ids = _reciprocal_rank_fusion(dense_hits, sparse_hits, k=60)
        core_results = self._hydrate(fused_ids, filters)[: self._top_k]

        # Small-to-big: expand with sibling and definition chunks
        expanded = self._expand_context(core_results)
        return expanded

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
            if filters.version_ids and r.version_id not in filters.version_ids:
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
                    heading_path=list(r.heading_path or []),
                )
            )
        return out

    # ------------------------------------------------------------------
    # Small-to-big context expansion
    # ------------------------------------------------------------------

    def _expand_context(
        self, core: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Expand retrieved chunks with sibling article chunks and definitions.

        For each core chunk that belongs to an article (has a heading_path with
        an Article/§ entry), fetch sibling chunks from the same version+article
        and insert them around the core chunk in document order.

        Also pull in definition chunks from the same version when a core chunk's
        cross_refs reference defined terms.
        """
        if not core:
            return core

        seen_ids: set[int] = {c.chunk_id for c in core}
        expansions: list[RetrievedChunk] = []

        # Collect all version_ids and article-level heading_paths we need siblings for
        sibling_keys: set[tuple[int, str]] = set()  # (version_id, article_heading)
        definition_version_ids: set[int] = set()

        for c in core:
            article_heading = _article_from_path(c.heading_path)
            if article_heading:
                sibling_keys.add((c.version_id, article_heading))

        # Batch-load candidate siblings
        if sibling_keys:
            version_ids = {vk[0] for vk in sibling_keys}
            candidates = (
                self._session.query(DocumentChunk)
                .filter(
                    DocumentChunk.version_id.in_(version_ids),
                    DocumentChunk.chunk_id.notin_(seen_ids),
                )
                .order_by(DocumentChunk.chunk_index)
                .all()
            )
            for row in candidates:
                row_article = _article_from_path(row.heading_path or [])
                key = (row.version_id, row_article or "")
                if key in sibling_keys and row.chunk_id not in seen_ids:
                    expansions.append(RetrievedChunk(
                        chunk_id=row.chunk_id,
                        version_id=row.version_id,
                        regulation_id=row.regulation_id,
                        text=row.text,
                        is_ict=row.is_ict,
                        lifecycle_stage=row.lifecycle_stage,
                        score=0.0,
                        heading_path=list(row.heading_path or []),
                        is_expansion=True,
                    ))
                    seen_ids.add(row.chunk_id)

            # Also pull definition chunks from the same versions
            definition_version_ids = version_ids
        if definition_version_ids:
            def_rows = (
                self._session.query(DocumentChunk)
                .filter(
                    DocumentChunk.version_id.in_(definition_version_ids),
                    DocumentChunk.is_definition.is_(True),
                    DocumentChunk.chunk_id.notin_(seen_ids),
                )
                .order_by(DocumentChunk.chunk_index)
                .all()
            )
            for row in def_rows:
                expansions.append(RetrievedChunk(
                    chunk_id=row.chunk_id,
                    version_id=row.version_id,
                    regulation_id=row.regulation_id,
                    text=row.text,
                    is_ict=row.is_ict,
                    lifecycle_stage=row.lifecycle_stage,
                    score=0.0,
                    heading_path=list(row.heading_path or []),
                    is_expansion=True,
                ))
                seen_ids.add(row.chunk_id)

        # Merge: core chunks first (in their ranked order), then expansions
        # sorted by (version_id, chunk_index) for reading coherence.
        result = list(core)
        expansions.sort(
            key=lambda c: (c.version_id, next(
                (row.chunk_index for row in [
                    self._session.get(DocumentChunk, c.chunk_id)
                ] if row), 0,
            )),
        )
        result.extend(expansions)
        return result


def _article_from_path(path: list[str]) -> str | None:
    """Extract the Article/§ heading from a heading_path list."""
    for h in reversed(path):
        if re.match(r"(?:Article|Artikel|Art\.?)\s+\d+", h, re.IGNORECASE):
            return h
        if re.match(r"§\s*\d+", h):
            return h
    return None


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
