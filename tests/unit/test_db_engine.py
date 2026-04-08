from pathlib import Path

from sqlalchemy import text

from regwatch.db.engine import create_app_engine


def test_engine_loads_sqlite_vec(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    engine = create_app_engine(db_file)

    with engine.connect() as conn:
        result = conn.execute(text("SELECT vec_version()")).scalar()
        assert result is not None
        assert isinstance(result, str)


def test_engine_enables_fts5(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    engine = create_app_engine(db_file)

    with engine.connect() as conn:
        conn.execute(text("CREATE VIRTUAL TABLE t USING fts5(content)"))
        conn.execute(text("INSERT INTO t(content) VALUES ('hello world')"))
        result = conn.execute(text("SELECT content FROM t WHERE t MATCH 'hello'")).scalar()
        assert result == "hello world"


def test_engine_enables_foreign_keys(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    engine = create_app_engine(db_file)

    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert result == 1
