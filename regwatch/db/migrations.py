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
