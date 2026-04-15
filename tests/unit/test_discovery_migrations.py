"""One-shot migration: entity_types (JSON list) -> entity_type + content_type."""
from __future__ import annotations

from sqlalchemy import create_engine, text

from regwatch.db.migrations import migrate_discovery_run_item_columns


def test_migrate_copies_first_entity_and_defaults_content_type(tmp_path):
    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    # Simulate pre-migration schema.
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE discovery_run (
                run_id INTEGER PRIMARY KEY,
                status TEXT, started_at TEXT, triggered_by TEXT,
                entity_types TEXT, mode TEXT,
                total_scraped INTEGER DEFAULT 0, new_count INTEGER DEFAULT 0,
                amended_count INTEGER DEFAULT 0, updated_count INTEGER DEFAULT 0,
                unchanged_count INTEGER DEFAULT 0, withdrawn_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                error_summary TEXT
            )
        """))
        c.execute(text("""
            CREATE TABLE discovery_run_item (
                item_id INTEGER PRIMARY KEY,
                run_id INTEGER,
                regulation_id INTEGER,
                reference_number TEXT,
                outcome TEXT,
                detail_url TEXT,
                entity_types TEXT,
                note TEXT,
                created_at TEXT
            )
        """))
        c.execute(text(
            "INSERT INTO discovery_run"
            " (run_id, status, started_at, triggered_by, entity_types, mode)"
            " VALUES (1, 'SUCCESS', '2026-04-14', 'USER_CLI', '[\"AIFM\"]', 'full')"
        ))
        c.execute(text(
            "INSERT INTO discovery_run_item"
            " (item_id, run_id, regulation_id, reference_number,"
            "  outcome, detail_url, entity_types, note, created_at)"
            " VALUES (1, 1, NULL, 'CSSF 22/806', 'NEW',"
            "  'https://x', '[\"AIFM\"]', NULL, '2026-04-14')"
        ))

    migrate_discovery_run_item_columns(engine)

    with engine.connect() as c:
        row = c.execute(text(
            "SELECT entity_type, content_type FROM discovery_run_item WHERE item_id=1"
        )).one()
        assert row.entity_type == "AIFM"
        assert row.content_type == "circulars-cssf"
        cols = [r[1] for r in c.execute(text("PRAGMA table_info(discovery_run_item)"))]
        assert "entity_types" not in cols
        assert "entity_type" in cols
        assert "content_type" in cols


def test_migrate_is_idempotent(tmp_path):
    """Running the migration twice must not fail or duplicate work."""
    db = tmp_path / "idempotent.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE discovery_run_item (
                item_id INTEGER PRIMARY KEY,
                run_id INTEGER,
                regulation_id INTEGER,
                reference_number TEXT,
                outcome TEXT,
                detail_url TEXT,
                entity_type TEXT,
                content_type TEXT,
                note TEXT,
                created_at TEXT
            )
        """))
    migrate_discovery_run_item_columns(engine)  # no-op
    migrate_discovery_run_item_columns(engine)  # no-op


def test_migrate_handles_missing_table(tmp_path):
    """Empty DB where the table doesn't even exist yet — no-op."""
    db = tmp_path / "empty.db"
    engine = create_engine(f"sqlite:///{db}")
    migrate_discovery_run_item_columns(engine)
