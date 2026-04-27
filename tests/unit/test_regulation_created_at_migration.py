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
