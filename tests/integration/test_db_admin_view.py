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


def _legacy_upload(tmp_path: Path, name: str = "legacy-upload.db") -> Path:
    """A backup file whose schema predates `regulation.created_at`."""
    path = tmp_path / name
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
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
        )
        conn.execute(
            """
            INSERT INTO regulation
                (type, reference_number, title, issuing_authority,
                 lifecycle_stage, is_ict, url, source_of_truth)
            VALUES
                ('CSSF_CIRCULAR', 'LEGACY 1/1', 'Legacy', 'CSSF',
                 'IN_FORCE', 0, 'https://example.com', 'SEED')
            """
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_import_of_older_schema_backup_keeps_app_usable(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: importing a backup from an older app version 500'd every
    page afterwards (`no such column: regulation.created_at`) because the
    import path copied the file without running the startup schema upgrade.
    """
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db", ref="ORIGINAL 1/1")

    upload = _legacy_upload(tmp_path)
    with open(upload, "rb") as f:
        r = client.post(
            "/settings/db/import",
            files={"file": (upload.name, f, "application/x-sqlite3")},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "db_action=imported" in r.headers["location"]

    # The redirect target and the dashboard must both render, in the same
    # process — no restart.
    assert client.get("/settings?db_action=imported").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/catalog").status_code == 200

    # The imported payload replaced the original and survived the upgrade.
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
    assert refs == {"LEGACY 1/1"}


def test_import_ignores_path_in_uploaded_filename(tmp_path: Path, monkeypatch) -> None:
    """The client-supplied filename used to become part of the temp path:
    "../../victim.txt" was overwritten with the upload and then deleted."""
    import tempfile

    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")

    r = client.post(
        "/settings/db/import",
        files={"file": ("../../victim.txt", b"not a database", "application/x-sqlite3")},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert victim.read_text() == "keep me"
