import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from fastapi.testclient import TestClient

from regwatch.db.engine import create_app_engine
from regwatch.db.models import UpdateEvent
from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import REGISTRY


def _cssf_only_client(tmp_path: Path, monkeypatch) -> TestClient:
    """Like `test_app_smoke._client` but disables every source except cssf_rss.

    The default config enables every fetch source, and the real sources hit
    the network in `fetch()` — this helper keeps only `cssf_rss` enabled so
    we can patch it with a fake in-process source.
    """
    shutil.copy("config.example.yaml", tmp_path / "config.yaml")
    cfg_path = tmp_path / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["paths"]["db_file"] = str(tmp_path / "app.db")
    data["paths"]["pdf_archive"] = str(tmp_path / "pdfs")
    data["paths"]["uploads_dir"] = str(tmp_path / "uploads")
    for name, src in data["sources"].items():
        src["enabled"] = name == "cssf_rss"
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "uploads").mkdir()
    cfg_path.write_text(yaml.safe_dump(data))

    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))

    import importlib

    import regwatch.main as main_module

    importlib.reload(main_module)
    return TestClient(main_module.create_app())


class _FakeCssfRssSource:
    """Replacement for the real CSSF RSS source used in these tests."""

    name = "cssf_rss"

    def __init__(self, keywords: list[str]) -> None:
        self.keywords = keywords

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        now = datetime.now(UTC)
        yield RawDocument(
            source="cssf_rss",
            source_url="https://example.com/fake",
            title="Manual run: fake CSSF update",
            published_at=now,
            raw_payload={"html_text": "Generic compliance text."},
            fetched_at=now,
        )


def _patch_registry_and_ollama(client, monkeypatch) -> None:
    # Make sure the real class is registered before we overwrite the entry.
    import regwatch.pipeline.fetch.cssf_rss  # noqa: F401

    monkeypatch.setitem(REGISTRY, "cssf_rss", _FakeCssfRssSource)

    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [0.0] * 768
    fake_ollama.chat.return_value = "[]"
    fake_ollama.health.return_value = MagicMock(
        reachable=True,
        chat_model_available=True,
        embedding_model_available=True,
    )
    client.app.state.ollama_client = fake_ollama


def test_run_pipeline_button_posts_and_creates_events(
    tmp_path: Path, monkeypatch
) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)
    _patch_registry_and_ollama(client, monkeypatch)

    response = client.post("/run-pipeline", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/?ran=")

    engine = create_app_engine(tmp_path / "app.db")
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        events = session.query(UpdateEvent).all()
        assert len(events) == 1
        assert events[0].title == "Manual run: fake CSSF update"


def test_dashboard_renders_button_and_flash(
    tmp_path: Path, monkeypatch
) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)

    # Plain dashboard: button visible, no flash.
    r = client.get("/")
    assert r.status_code == 200
    assert "Run pipeline now" in r.text
    assert "Pipeline run #" not in r.text

    # Dashboard with success flash.
    r2 = client.get("/?ran=7&events=3")
    assert r2.status_code == 200
    assert "Pipeline run #7 completed" in r2.text
    assert "3 new event(s)" in r2.text


def test_run_pipeline_error_redirects_with_error_flash(
    tmp_path: Path, monkeypatch
) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)

    # Force build_enabled_sources to raise.
    import regwatch.web.routes.actions as actions_module

    def boom(_cfg):  # type: ignore[no-untyped-def]
        raise RuntimeError("kaboom")

    monkeypatch.setattr(actions_module, "build_enabled_sources", boom)

    r = client.post("/run-pipeline", follow_redirects=False)
    assert r.status_code == 303
    assert "pipeline_error=RuntimeError" in r.headers["location"]
