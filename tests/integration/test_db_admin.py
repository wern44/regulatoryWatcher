import sqlite3
from pathlib import Path
from textwrap import dedent

import pytest
from sqlalchemy.orm import Session

from regwatch.db.admin import (
    backup_database,
    reset_database,
    restore_database,
    validate_uploaded_database,
)
from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables


def _seeded_db(tmp_path: Path, name: str = "src.db") -> Path:
    db_path = tmp_path / name
    engine = create_app_engine(db_path)
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    with Session(engine) as session:
        session.add(
            Regulation(
                type=RegulationType.CSSF_CIRCULAR,
                reference_number="CSSF 18/698",
                title="Sample",
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=False,
                source_of_truth="SEED",
                url="https://example.com",
            )
        )
        session.commit()
    engine.dispose()
    return db_path


def test_backup_creates_consistent_copy(tmp_path: Path) -> None:
    src = _seeded_db(tmp_path)
    dest = tmp_path / "backup" / "snapshot.db"

    result = backup_database(src, dest)
    assert result == dest
    assert dest.exists()

    # Backup file is a self-contained SQLite database.
    conn = sqlite3.connect(str(dest))
    try:
        rows = conn.execute(
            "SELECT reference_number FROM regulation"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("CSSF 18/698",)]


def test_validate_rejects_non_sqlite_file(tmp_path: Path) -> None:
    bogus = tmp_path / "not_a_db.txt"
    bogus.write_text("hello world")
    with pytest.raises(ValueError, match="Cannot read uploaded database|Not a valid"):
        validate_uploaded_database(bogus)


def test_validate_rejects_unrelated_sqlite_db(tmp_path: Path) -> None:
    other = tmp_path / "other.db"
    conn = sqlite3.connect(str(other))
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError, match="regulation"):
        validate_uploaded_database(other)


def test_restore_replaces_target_database(tmp_path: Path) -> None:
    target = _seeded_db(tmp_path, "target.db")
    source = _seeded_db(tmp_path, "source.db")

    # Differentiate the source so we can prove the restore landed.
    src_engine = create_app_engine(source)
    with Session(src_engine) as session:
        session.add(
            Regulation(
                type=RegulationType.EU_REGULATION,
                reference_number="DORA",
                title="DORA",
                issuing_authority="EU",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=True,
                source_of_truth="SEED",
                url="https://example.com",
            )
        )
        session.commit()
    src_engine.dispose()

    target_engine = create_app_engine(target)
    restore_database(target_engine, uploaded_file=source, db_path=target)

    # Re-create the engine because dispose was called.
    target_engine = create_app_engine(target)
    with Session(target_engine) as session:
        refs = {
            r[0]
            for r in session.execute(
                Regulation.__table__.select().with_only_columns(
                    Regulation.reference_number
                )
            )
        }
    assert refs == {"CSSF 18/698", "DORA"}


def test_reset_drops_all_data_and_optionally_seeds(tmp_path: Path) -> None:
    db_path = _seeded_db(tmp_path)
    seed_file = tmp_path / "seed.yaml"
    seed_file.write_text(
        dedent(
            """
            entity:
              lei: "TEST1234567890123456"
              legal_name: "Test"
            authorizations:
              - type: AIFM
                cssf_entity_id: "1"
            regulations:
              - reference_number: "TEST 1/1"
                type: CSSF_CIRCULAR
                title: "Reset test"
                issuing_authority: "CSSF"
                lifecycle_stage: IN_FORCE
                is_ict: false
                url: "https://example.com"
                applicability: BOTH
                aliases: []
            """
        )
    )

    engine = create_app_engine(db_path)
    reset_database(engine, embedding_dim=4, seed_file=seed_file)

    with Session(engine) as session:
        rows = session.execute(
            Regulation.__table__.select().with_only_columns(
                Regulation.reference_number
            )
        ).all()
        refs = {r[0] for r in rows}
    assert refs == {"TEST 1/1"}

    # Virtual tables exist and are empty.
    with engine.connect() as conn:
        from sqlalchemy import text as sa_text

        assert (
            conn.execute(
                sa_text("SELECT COUNT(*) FROM document_chunk_vec")
            ).scalar()
            == 0
        )
        assert (
            conn.execute(
                sa_text("SELECT COUNT(*) FROM document_chunk_fts")
            ).scalar()
            == 0
        )


def test_reset_without_seed_file_leaves_catalog_empty(tmp_path: Path) -> None:
    db_path = _seeded_db(tmp_path)

    engine = create_app_engine(db_path)
    reset_database(engine, embedding_dim=4, seed_file=None)

    with Session(engine) as session:
        count = session.execute(
            Regulation.__table__.select()
        ).all()
    assert count == []
