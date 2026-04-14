"""Lightweight additive schema sync for SQLite.

Adds columns declared on ORM models that are missing from the live database,
so model changes don't require a manual ALTER TABLE when `create_all` runs
against an older file (which only creates missing tables, not missing columns).

This deliberately only *adds* — it never drops or alters existing columns —
so it's safe to run on every app start. Destructive migrations still need
explicit handling.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Engine, MetaData, inspect, text
from sqlalchemy.schema import Column, ColumnDefault, CreateColumn


def _literal_default(col: Column[object]) -> str | None:
    """Produce a SQL literal for a NOT NULL column added to a populated table."""
    if col.server_default is not None:
        return None  # CreateColumn will render it
    default = col.default
    if isinstance(default, ColumnDefault) and default.is_scalar:
        val = default.arg
        if isinstance(val, bool):
            return "1" if val else "0"
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, str):
            escaped = val.replace("'", "''")
            return f"'{escaped}'"
    t = col.type
    if isinstance(t, sa.Boolean):
        return "0"
    if isinstance(t, (sa.Integer, sa.Float)):
        return "0"
    if isinstance(t, (sa.String, sa.Text)):
        return "''"
    return None


def sync_schema(engine: Engine, metadata: MetaData) -> list[tuple[str, str]]:
    """Add any ORM-declared columns that are missing from the live DB.

    Returns the list of `(table, column)` tuples that were added.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[tuple[str, str]] = []
    dialect = engine.dialect

    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all handles wholly-missing tables
            db_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in db_cols:
                    continue
                col_ddl = str(CreateColumn(col).compile(dialect=dialect))
                if not col.nullable and col.server_default is None:
                    lit = _literal_default(col)
                    if lit is not None:
                        col_ddl = f"{col_ddl} DEFAULT {lit}"
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col_ddl}"))
                added.append((table.name, col.name))
    return added
