"""The canonical "bring this database up to date" sequence.

Every entry point that opens the app database — the FastAPI factory, the
`init-db` CLI command, and the import/reset admin paths — must run the same
steps in the same order, or a database is left half-upgraded and the next
query fails with `no such column` / `no such table`. Keeping the sequence in
one place is the only way to guarantee that.
"""
from __future__ import annotations

import logging

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from regwatch.db.entity_type_seed import seed_default_entity_types
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.migrations import (
    migrate_authorization_type_drop_check,
    migrate_discovery_run_item_columns,
    migrate_regulation_created_at,
)
from regwatch.db.models import Base
from regwatch.db.schema_sync import sync_schema
from regwatch.db.virtual_tables import create_virtual_tables

logger = logging.getLogger(__name__)


def upgrade_schema(engine: Engine, *, embedding_dim: int) -> None:
    """Create missing tables, then apply every migration, on any-vintage DB.

    Safe and idempotent on a fresh file, on the current schema, and on a
    database written by an older version of the app.
    """
    Base.metadata.create_all(engine)
    # Run column-adding migrations BEFORE sync_schema. sync_schema generates
    # NOT NULL ADD COLUMN statements with no DEFAULT for DateTime types,
    # which SQLite rejects on populated tables; the migration adds the
    # column as nullable and backfills, after which sync_schema is a no-op
    # for that column.
    migrate_authorization_type_drop_check(engine)
    migrate_regulation_created_at(engine)
    sync_schema(engine, Base.metadata)
    create_virtual_tables(engine, embedding_dim=embedding_dim)
    migrate_discovery_run_item_columns(engine)


def seed_defaults(engine: Engine) -> None:
    """Insert the rows the app cannot run without (entity types, core fields).

    Both seeders no-op when their table already has rows, so this is safe on
    every start and after an import.
    """
    with Session(engine) as session:
        seed_default_entity_types(session)
        seed_core_fields(session)
        session.commit()
