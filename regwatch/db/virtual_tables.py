"""Create virtual tables that SQLAlchemy declarative does not manage."""
from __future__ import annotations

from sqlalchemy import Engine, text


def create_virtual_tables(engine: Engine, *, embedding_dim: int) -> None:
    """Create `document_chunk_vec` (sqlite-vec) and `document_chunk_fts` (FTS5) if missing."""
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunk_vec USING vec0(
                    chunk_id INTEGER PRIMARY KEY,
                    embedding float[{embedding_dim}]
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunk_fts USING fts5(
                    text,
                    content='document_chunk',
                    content_rowid='chunk_id'
                )
                """
            )
        )
