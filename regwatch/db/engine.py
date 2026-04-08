"""SQLAlchemy engine factory with sqlite-vec and FTS5 loaded."""
from __future__ import annotations

from pathlib import Path

import sqlite_vec
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry


def create_app_engine(db_file: Path | str) -> Engine:
    """Create a SQLAlchemy engine against a SQLite file with sqlite-vec and FTS5 loaded."""
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite:///{db_file.as_posix()}"
    engine = create_engine(url, future=True)

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
        cursor.close()

    return engine
