"""One-shot, idempotent schema migrations run at engine init time.

We don't use Alembic (see CLAUDE.md). `Base.metadata.create_all` covers
additive column/table changes automatically; only renames and data
copies need explicit migration code here.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)


def migrate_discovery_run_item_columns(engine: Engine) -> None:
    """Rename discovery_run_item.entity_types (JSON list) -> entity_type,
    and add content_type (default 'circulars-cssf' for legacy rows).

    Idempotent: detects already-migrated schema and returns cleanly. Safe
    to call on a fresh DB where the table doesn't exist yet.
    """
    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(discovery_run_item)"))]
        if not cols:
            return  # Table doesn't exist yet; create_all handles fresh DBs.
        if "entity_types" not in cols:
            return  # Already migrated.
        if "entity_type" in cols:
            # Partial prior migration: columns were added but DROP never ran.
            # Resume from the drop and exit.
            conn.execute(text("ALTER TABLE discovery_run_item DROP COLUMN entity_types"))
            logger.info("Resumed partial migration by dropping stale entity_types column")
            return

        logger.info(
            "Migrating discovery_run_item: entity_types -> entity_type + content_type"
        )
        conn.execute(text("ALTER TABLE discovery_run_item ADD COLUMN entity_type VARCHAR(40)"))
        conn.execute(text("ALTER TABLE discovery_run_item ADD COLUMN content_type VARCHAR(60)"))

        rows = conn.execute(text(
            "SELECT item_id, entity_types FROM discovery_run_item"
        )).all()
        for item_id, raw in rows:
            first = ""
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list) and parsed:
                        first = str(parsed[0])
                except (ValueError, TypeError):
                    first = ""
            conn.execute(
                text(
                    "UPDATE discovery_run_item SET entity_type = :et, "
                    "content_type = :ct WHERE item_id = :id"
                ),
                {"et": first, "ct": "circulars-cssf", "id": item_id},
            )
        conn.execute(text("ALTER TABLE discovery_run_item DROP COLUMN entity_types"))
        logger.info("Migrated %d discovery_run_item rows", len(rows))


def migrate_regulation_created_at(engine: Engine) -> None:
    """Add regulation.created_at and backfill existing rows to the migration time.

    Backfilling to NOW() at migration time means existing regulations
    will not count as 'new' once the user has visited each section once.
    Idempotent: returns cleanly if the column already exists or the
    table doesn't exist yet.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(regulation)"))]
        if not cols:
            return  # fresh DB; create_all handles it
        if "created_at" in cols:
            return  # already migrated

        now_iso = datetime.now(UTC).isoformat()
        conn.execute(text("ALTER TABLE regulation ADD COLUMN created_at DATETIME"))
        result = conn.execute(
            text("UPDATE regulation SET created_at = :ts WHERE created_at IS NULL"),
            {"ts": now_iso},
        )
        logger.info(
            "Backfilled regulation.created_at on %d existing rows", result.rowcount
        )


def migrate_authorization_type_drop_check(engine: Engine) -> None:
    """Remove the legacy CHECK(type IN ('AIFM','CHAPTER15_MANCO')) constraint on
    authorization.type so new entity-type slugs can be inserted.

    SQLite has no DROP CONSTRAINT — we use the table-rewrite pattern:
    rename old table, create new (without CHECK), copy rows, drop old.

    Idempotent: returns cleanly if the table doesn't exist or the
    constraint is already gone.
    """
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='authorization'"
        )).first()
        if row is None:
            return  # fresh DB; create_all handles it
        ddl = (row[0] or "")
        if "CHECK" not in ddl.upper() or "AIFM" not in ddl.upper():
            return  # already migrated or never had the constraint

        logger.info("Migrating authorization table to drop legacy type CHECK")

        # Capture column list so the INSERT SELECT below copies every column
        # (a future column add would otherwise be silently dropped).
        col_rows = conn.execute(text("PRAGMA table_info(authorization)")).all()
        cols = [r[1] for r in col_rows]
        col_list = ", ".join(cols)

        conn.execute(text(
            "ALTER TABLE authorization RENAME TO _authorization_old"
        ))
        # Recreate the table with the canonical (no-CHECK) shape.
        # We intentionally hand-write the DDL rather than relying on
        # Base.metadata so this migration works against any future model
        # tweak; the *intent* is to drop a constraint, not refresh schema.
        conn.execute(text("""
            CREATE TABLE authorization (
                authorization_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                lei VARCHAR(20) NOT NULL,
                type VARCHAR(20) NOT NULL,
                cssf_entity_id VARCHAR(20),
                authorization_date DATE,
                status VARCHAR(50),
                cssf_url VARCHAR(500),
                FOREIGN KEY(lei) REFERENCES entity (lei),
                CONSTRAINT uq_authorization_lei_type UNIQUE (lei, type)
            )
        """))
        conn.execute(text(
            f"INSERT INTO authorization ({col_list}) "
            f"SELECT {col_list} FROM _authorization_old"
        ))
        conn.execute(text("DROP TABLE _authorization_old"))
        logger.info("authorization table migrated; CHECK constraint dropped")
