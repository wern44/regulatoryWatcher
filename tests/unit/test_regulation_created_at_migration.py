"""Tests for the regulation.created_at backfill migration."""
from datetime import UTC, datetime

from sqlalchemy import create_engine, text


def _engine_with_old_regulation_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE regulation (
                regulation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                type VARCHAR(50) NOT NULL,
                reference_number VARCHAR(100) NOT NULL,
                title TEXT NOT NULL,
                issuing_authority VARCHAR(100) NOT NULL,
                lifecycle_stage VARCHAR(40) NOT NULL,
                is_ict BOOLEAN DEFAULT 0,
                url VARCHAR(500) NOT NULL,
                source_of_truth VARCHAR(20) NOT NULL
            )
            """
        ))
        conn.execute(text(
            """
            INSERT INTO regulation
            (type, reference_number, title, issuing_authority, lifecycle_stage,
             is_ict, url, source_of_truth)
            VALUES
            ('CSSF_CIRCULAR', 'CSSF 18/698', 't1', 'CSSF', 'IN_FORCE',
             0, 'https://example.com/1', 'SEED'),
            ('CSSF_CIRCULAR', 'CSSF 20/750', 't2', 'CSSF', 'IN_FORCE',
             1, 'https://example.com/2', 'SEED')
            """
        ))
    return engine


def test_migration_adds_column_and_backfills(tmp_path):
    from regwatch.db.migrations import migrate_regulation_created_at

    engine = _engine_with_old_regulation_table(tmp_path)
    before = datetime.now(UTC)
    migrate_regulation_created_at(engine)
    after = datetime.now(UTC)

    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(regulation)"))]
        assert "created_at" in cols
        rows = conn.execute(text("SELECT created_at FROM regulation")).all()
        assert len(rows) == 2
        for (ts_str,) in rows:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            assert before <= ts <= after


def test_migration_is_idempotent(tmp_path):
    from regwatch.db.migrations import migrate_regulation_created_at

    engine = _engine_with_old_regulation_table(tmp_path)
    migrate_regulation_created_at(engine)
    migrate_regulation_created_at(engine)  # second call must be a no-op


def test_migration_handles_missing_table(tmp_path):
    from regwatch.db.migrations import migrate_regulation_created_at

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    migrate_regulation_created_at(engine)  # must not raise


def test_migration_runs_before_sync_schema_does_not_blow_up(tmp_path):
    """Regression: sync_schema generates `ADD COLUMN created_at DATETIME NOT NULL`
    with no DEFAULT (because _literal_default has no DateTime case), which SQLite
    rejects on a populated table. The migration must run first so the column is
    already present (nullable, backfilled) when sync_schema looks.
    """
    from regwatch.db.migrations import migrate_regulation_created_at
    from regwatch.db.models import Base
    from regwatch.db.schema_sync import sync_schema

    engine = _engine_with_old_regulation_table(tmp_path)

    # Order matches regwatch/main.py::create_app: migration FIRST.
    migrate_regulation_created_at(engine)
    sync_schema(engine, Base.metadata)  # must not raise

    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(regulation)"))]
        assert "created_at" in cols


def test_sync_schema_first_would_fail(tmp_path):
    """Documents the failure mode the prior test guards against: running
    sync_schema BEFORE the migration on a populated old-schema DB raises
    `Cannot add a NOT NULL column with default value NULL`.

    If this test starts passing without raising, sync_schema has been taught
    to handle DateTime columns and the migration's ordering may no longer
    be load-bearing.
    """
    import pytest
    from sqlalchemy.exc import OperationalError

    from regwatch.db.models import Base
    from regwatch.db.schema_sync import sync_schema

    engine = _engine_with_old_regulation_table(tmp_path)

    with pytest.raises(OperationalError, match="NOT NULL"):
        sync_schema(engine, Base.metadata)
