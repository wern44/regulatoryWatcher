from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base
from regwatch.services.settings import SettingsService


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_get_returns_none_when_key_missing(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    assert svc.get("nonexistent") is None


def test_get_returns_default_when_key_missing(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    assert svc.get("nonexistent", default="fallback") == "fallback"


def test_set_and_get_roundtrip(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    svc.set("chat_model", "llama3.1:latest")
    session.commit()
    assert svc.get("chat_model") == "llama3.1:latest"


def test_set_overwrites_existing_value(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    svc.set("chat_model", "first")
    session.commit()
    svc.set("chat_model", "second")
    session.commit()
    assert svc.get("chat_model") == "second"


def test_get_all_returns_all_pairs(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    svc.set("chat_model", "llama3.1:latest")
    svc.set("embedding_model", "nomic-embed-text")
    session.commit()
    result = svc.get_all()
    assert result == {
        "chat_model": "llama3.1:latest",
        "embedding_model": "nomic-embed-text",
    }
