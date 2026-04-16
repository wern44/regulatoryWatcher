"""SQLAlchemy engine factory with sqlite-vec and FTS5 loaded."""
from __future__ import annotations

from pathlib import Path

import sqlite_vec
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry, NullPool


def create_app_engine(db_file: Path | str) -> Engine:
    """Create a SQLAlchemy engine against a SQLite file with sqlite-vec and FTS5 loaded."""
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite:///{db_file.as_posix()}"
    # NullPool: every Session gets a fresh DBAPI connection and returns it on
    # close. This avoids two problems: (1) a long-running uvicorn worker
    # holding a stale transaction from an earlier failed request, and
    # (2) PRAGMA settings on this module being applied to brand-new
    # connections only, so changing them requires a full worker restart.
    engine = create_engine(url, future=True, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: DBAPIConnection, _: ConnectionPoolEntry) -> None:
        # sqlite-vec requires enable_load_extension before load_extension.
        dbapi_conn.enable_load_extension(True)
        sqlite_vec.load(dbapi_conn)
        dbapi_conn.enable_load_extension(False)

        # Enable foreign keys and configure reasonable defaults.
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # Wait up to 30s for another writer to release the lock instead of
        # failing immediately with "database is locked". This matters when the
        # uvicorn worker, CLI commands, background analysis threads, and any
        # ad-hoc scripts all share the same SQLite file.
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine
