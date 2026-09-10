"""Database admin helpers: online backup, file restore, and reset."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from regwatch.db.bootstrap import seed_defaults, upgrade_schema
from regwatch.db.models import Base
from regwatch.db.seed import load_seed


def backup_database(db_path: Path | str, dest_path: Path | str) -> Path:
    """Create a consistent online copy of the SQLite database at `dest_path`.

    Uses SQLite's backup API so the copy is safe even while another process
    has the source open.
    """
    db_path = Path(db_path)
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    src_conn = sqlite3.connect(str(db_path))
    try:
        dst_conn = sqlite3.connect(str(dest_path))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return dest_path


def validate_uploaded_database(uploaded_file: Path | str) -> None:
    """Confirm `uploaded_file` is a SQLite database with the expected schema.

    Raises ValueError with a human-readable message on failure.
    """
    uploaded_file = Path(uploaded_file)
    try:
        check_conn = sqlite3.connect(str(uploaded_file))
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Not a valid SQLite database: {exc}") from exc
    try:
        try:
            row = check_conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='regulation'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"Cannot read uploaded database: {exc}") from exc
        if row is None:
            raise ValueError(
                "Uploaded file does not contain a 'regulation' table — "
                "this does not look like a Regulatory Watcher backup."
            )
    finally:
        check_conn.close()


def restore_database(
    engine: Engine,
    uploaded_file: Path | str,
    db_path: Path | str,
    *,
    embedding_dim: int,
) -> None:
    """Replace the app database file with `uploaded_file`.

    Validates the upload, disposes the engine's pool so the file is not held
    open, then overwrites the target file in place. Subsequent sessions
    against the same engine URL will read the new content.

    The uploaded backup may have been exported by an older version of the app,
    so the restored file is put through the same schema upgrade and default
    seeding the app runs at startup. Without that, every request after an
    import fails (`no such column: regulation.created_at`) until the process
    is restarted.
    """
    uploaded_file = Path(uploaded_file)
    db_path = Path(db_path)

    validate_uploaded_database(uploaded_file)

    # Close any pool connections before overwriting (no-op for NullPool, but
    # safe to call regardless).
    engine.dispose()
    shutil.copy2(str(uploaded_file), str(db_path))

    # Drop the WAL/SHM sidecars from the previous database — they are stale
    # against the freshly copied file and SQLite will complain.
    for sidecar in (
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    ):
        if sidecar.exists():
            sidecar.unlink()

    # The restored file is now the live database — upgrade it in place.
    upgrade_schema(engine, embedding_dim=embedding_dim)
    seed_defaults(engine)


def reset_database(
    engine: Engine,
    *,
    embedding_dim: int,
    seed_file: Path | str | None = None,
) -> None:
    """Drop every table, recreate the schema + virtual tables, optionally re-seed."""
    # Drop the FTS/vec virtual tables before dropping the ORM tables: the
    # FTS5 triggers reference document_chunk_fts, so we have to remove the
    # virtual tables first or the trigger drop chains will be unhappy.
    with engine.begin() as conn:
        for t in ("document_chunk_fts", "document_chunk_vec"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))

    Base.metadata.drop_all(engine)
    upgrade_schema(engine, embedding_dim=embedding_dim)
    seed_defaults(engine)

    if seed_file is not None:
        seed_path = Path(seed_file)
        if seed_path.exists():
            with Session(engine) as session:
                load_seed(session, seed_path)
                session.commit()
