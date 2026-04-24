import shutil
import time
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
    app = main_module.create_app()
    # Provide a dummy model so FirstStartupMiddleware does not redirect test requests.
    app.state.llm_client.chat_model = "test-model"
    return TestClient(app)


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


def _patch_registry_and_llm(client, monkeypatch) -> None:
    # Make sure the real class is registered before we overwrite the entry.
    import regwatch.pipeline.fetch.cssf_rss  # noqa: F401

    monkeypatch.setitem(REGISTRY, "cssf_rss", _FakeCssfRssSource)

    fake_llm = MagicMock()
    fake_llm.embed.return_value = [0.0] * 768
    fake_llm.chat.return_value = "[]"
    fake_llm.health.return_value = MagicMock(
        reachable=True,
        chat_model_available=True,
        embedding_model_available=True,
    )
    client.app.state.llm_client = fake_llm


def _wait_until_idle_or_done(client, timeout: float = 5.0) -> dict:
    """Poll the progress object until status is no longer 'running'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = client.app.state.pipeline_progress.snapshot()
        if snap["status"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError(
        f"pipeline_progress did not finish within {timeout}s "
        f"(last status: {snap['status']})"
    )


def test_post_returns_progress_widget_and_starts_background_run(
    tmp_path: Path, monkeypatch
) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)
    _patch_registry_and_llm(client, monkeypatch)

    response = client.post("/run-pipeline")
    assert response.status_code == 200
    # The body is the progress partial, not a redirect.
    assert 'id="pipeline-progress"' in response.text
    assert "Pipeline running" in response.text or "Pipeline completed" in response.text

    # Wait for the background thread to finish, then verify it actually
    # produced an event row.
    final = _wait_until_idle_or_done(client)
    assert final["status"] == "completed"
    assert final["events_created"] == 1

    engine = create_app_engine(tmp_path / "app.db")
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        events = session.query(UpdateEvent).all()
        assert len(events) == 1
        assert events[0].title == "Manual run: fake CSSF update"


def test_status_endpoint_returns_progress_widget(
    tmp_path: Path, monkeypatch
) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)
    _patch_registry_and_llm(client, monkeypatch)

    # Kick off a run and wait for it to settle.
    client.post("/run-pipeline")
    _wait_until_idle_or_done(client)

    r = client.get("/run-pipeline/status")
    assert r.status_code == 200
    assert 'id="pipeline-progress"' in r.text
    # Once finished the polling trigger must be gone — the widget should not
    # contain `hx-trigger="every 2s"`.
    assert "every 2s" not in r.text
    assert "Pipeline completed" in r.text


def test_dashboard_renders_run_button_and_progress_slot(
    tmp_path: Path, monkeypatch
) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)

    r = client.get("/")
    assert r.status_code == 200
    assert "Run all sources" in r.text
    assert 'id="pipeline-progress-slot"' in r.text
    # No run yet, so the slot is empty (status == idle is filtered out).
    assert 'id="pipeline-progress"' not in r.text


def test_concurrent_post_does_not_start_a_second_run(
    tmp_path: Path, monkeypatch
) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)
    _patch_registry_and_llm(client, monkeypatch)

    # Pre-mark the progress as running so the second POST sees it.
    client.app.state.pipeline_progress.reset_for_run(total_sources=1)

    r = client.post("/run-pipeline")
    assert r.status_code == 200
    assert 'id="pipeline-progress"' in r.text
    # The state we pre-set is still 'running' — no new background thread
    # was started, so progress is unchanged.
    snap = client.app.state.pipeline_progress.snapshot()
    assert snap["status"] == "running"
    assert snap["events_created"] == 0


def test_run_pipeline_error_marks_progress_as_failed(
    tmp_path: Path, monkeypatch
) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)

    # Force build_enabled_sources to raise.
    import regwatch.pipeline.run_helpers as run_helpers_module

    def boom(_cfg, **_kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("kaboom")

    monkeypatch.setattr(run_helpers_module, "build_enabled_sources", boom)

    client.post("/run-pipeline")
    final = _wait_until_idle_or_done(client)
    assert final["status"] == "failed"
    assert "RuntimeError" in (final["error"] or "")
