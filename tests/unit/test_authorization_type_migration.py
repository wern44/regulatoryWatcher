"""Migration removes the legacy CHECK constraint on authorization.type."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from regwatch.db.migrations import migrate_authorization_type_drop_check


@pytest.fixture
def legacy_db(tmp_path):
    """A SQLite DB with the OLD authorization-type CHECK constraint."""
    db = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE entity (lei VARCHAR(20) PRIMARY KEY, legal_name VARCHAR(255))
        """))
        conn.execute(text("""
            CREATE TABLE authorization (
                authorization_id INTEGER PRIMARY KEY,
                lei VARCHAR(20),
                type VARCHAR(15) CHECK (type IN ('AIFM', 'CHAPTER15_MANCO')),
                cssf_entity_id VARCHAR(20)
            )
        """))
        conn.execute(text(
            "INSERT INTO entity (lei, legal_name) VALUES ('TEST', 'Test')"
        ))
        conn.execute(text(
            "INSERT INTO authorization (lei, type) VALUES ('TEST', 'AIFM')"
        ))
    return engine


def test_legacy_db_rejects_new_slugs_before_migration(legacy_db):
    """Sanity check: the legacy CHECK is actually enforced."""
    with legacy_db.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO authorization (lei, type) VALUES ('TEST', 'PSF_SPECIALISED')"
            ))


def test_migration_drops_check_and_preserves_data(legacy_db):
    migrate_authorization_type_drop_check(legacy_db)
    with legacy_db.begin() as conn:
        rows = conn.execute(text("SELECT lei, type FROM authorization")).all()
        assert rows == [("TEST", "AIFM")]
        # And new slugs now insert successfully.
        conn.execute(text(
            "INSERT INTO authorization (lei, type) VALUES ('TEST', 'PSF_SPECIALISED')"
        ))


def test_migration_idempotent_on_clean_db(tmp_path):
    """Running on a DB without the legacy CHECK is a no-op."""
    db = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE authorization (
                authorization_id INTEGER PRIMARY KEY,
                lei VARCHAR(20),
                type VARCHAR(20),
                cssf_entity_id VARCHAR(20)
            )
        """))
        conn.execute(text(
            "INSERT INTO authorization (lei, type) VALUES ('X', 'PSF')"
        ))
    migrate_authorization_type_drop_check(engine)
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT type FROM authorization")).all()
        assert rows == [("PSF",)]


def test_migration_no_table_is_no_op(tmp_path):
    db = tmp_path / "empty.db"
    engine = create_engine(f"sqlite:///{db}")
    migrate_authorization_type_drop_check(engine)  # must not raise
