import sqlite3
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed(db_file: Path, *, ref: str = "CSSF 18/698") -> None:
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Regulation(
                type=RegulationType.CSSF_CIRCULAR,
                reference_number=ref,
                title=ref,
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=False,
                source_of_truth="SEED",
                url="https://example.com",
            )
        )
        session.commit()
    engine.dispose()


def test_export_returns_sqlite_file(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")

    r = client.get("/settings/db/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-sqlite3"
    assert r.headers["content-disposition"].startswith("attachment;")

    # The body must itself be a valid sqlite database that contains our row.
    snapshot = tmp_path / "downloaded.db"
    snapshot.write_bytes(r.content)
    conn = sqlite3.connect(str(snapshot))
    try:
        rows = conn.execute(
            "SELECT reference_number FROM regulation"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("CSSF 18/698",)]


def test_import_replaces_database(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db", ref="ORIGINAL 1/1")

    # Build a separate "uploaded" db with a different reference.
    upload_db = tmp_path / "upload.db"
    _seed(upload_db, ref="IMPORTED 2/2")

    with open(upload_db, "rb") as f:
        r = client.post(
            "/settings/db/import",
            files={"file": ("upload.db", f, "application/x-sqlite3")},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "db_action=imported" in r.headers["location"]

    # The live database should now contain the imported reference.
    engine = create_app_engine(tmp_path / "app.db")
    with Session(engine) as session:
        refs = {
            r[0]
            for r in session.execute(
                Regulation.__table__.select().with_only_columns(
                    Regulation.reference_number
                )
            )
        }
    assert "IMPORTED 2/2" in refs
    assert "ORIGINAL 1/1" not in refs


def test_import_rejects_non_database_file(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")

    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"this is not a sqlite database")

    with open(bogus, "rb") as f:
        r = client.post(
            "/settings/db/import",
            files={"file": ("bogus.db", f, "application/x-sqlite3")},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "db_error=ValueError" in r.headers["location"]


def test_reset_drops_user_data_and_re_seeds(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db", ref="WILL BE GONE")

    r = client.post("/settings/db/reset", follow_redirects=False)
    assert r.status_code == 303
    assert "db_action=reset" in r.headers["location"]

    engine = create_app_engine(tmp_path / "app.db")
    with Session(engine) as session:
        refs = {
            row[0]
            for row in session.execute(
                Regulation.__table__.select().with_only_columns(
                    Regulation.reference_number
                )
            )
        }
    # The pre-reset row is gone and the curated seed catalog has been
    # re-loaded from seeds/regulations_seed.yaml (resolved relative to cwd).
    assert "WILL BE GONE" not in refs
    assert len(refs) > 0


def test_settings_page_shows_db_section(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Download backup" in r.text
    assert "Restore from file" in r.text
    assert "Reset database" in r.text
