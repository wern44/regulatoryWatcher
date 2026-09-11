"""Only one long-running task at a time; a refused start says what is running.

SQLite allows a single writer, so two bulk jobs at once end in "database is
locked" (e.g. a CSSF discovery started while an ICT refresh was running).
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import unquote

from sqlalchemy import func, select

from regwatch.db.models import AnalysisRun, DiscoveryRun
from regwatch.web.task_guard import BUSY_COOKIE
from tests.integration.test_status_bar import _client


def _busy_message(resp) -> str:  # type: ignore[no-untyped-def]
    return unquote(resp.cookies.get(BUSY_COOKIE, ""))


def _count(client, model) -> int:  # type: ignore[no-untyped-def]
    with client.app.state.session_factory() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_catalog_refresh_refused_while_pipeline_runs(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.app.state.pipeline_progress.reset_for_run(total_sources=3)

    resp = client.post("/catalog/refresh", follow_redirects=False)

    assert resp.status_code == 303
    assert "pipeline run" in _busy_message(resp).lower()
    assert client.app.state.analysis_progress.status == "idle"


def test_cssf_discovery_refused_while_refresh_runs(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.app.state.analysis_progress.start(run_id=0, total=5, task="ICT refresh")

    resp = client.post(
        "/catalog/discover-cssf", data={"mode": "full"}, follow_redirects=False
    )

    assert resp.status_code == 303
    assert "ICT refresh" in _busy_message(resp)
    assert _count(client, DiscoveryRun) == 0


def test_cssf_discovery_refused_while_another_discovery_runs(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    client.app.state.cssf_discovery_progress.start(run_id=7)

    resp = client.post("/catalog/discover-cssf", data={"mode": "full"}, follow_redirects=False)

    assert "CSSF discovery" in _busy_message(resp)
    assert _count(client, DiscoveryRun) == 0


def test_analyse_refused_while_discovery_runs(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.app.state.cssf_discovery_progress.start(run_id=7)

    resp = client.post(
        "/catalog/analyse", data={"version_ids": ["1"]}, follow_redirects=False
    )

    assert "CSSF discovery" in _busy_message(resp)
    assert _count(client, AnalysisRun) == 0


def test_db_reset_refused_while_a_task_runs(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.app.state.analysis_progress.start(run_id=0, total=5, task="Catalog refresh")

    resp = client.post("/settings/db/reset", follow_redirects=False)

    assert resp.status_code == 303
    assert "Catalog refresh" in _busy_message(resp)
    assert "db_action=reset" not in resp.headers["location"]


def test_pipeline_refused_while_refresh_runs(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.app.state.analysis_progress.start(run_id=0, total=5, task="Catalog refresh")

    resp = client.post("/run-pipeline")

    assert "Catalog refresh is already running" in resp.text
    assert client.app.state.pipeline_progress.snapshot()["status"] == "idle"


def test_ict_refresh_runs_in_background_and_shows_in_status_bar(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    llm = MagicMock()
    llm.chat.side_effect = lambda **_: time.sleep(0.5) or "[]"
    client.app.state.llm_client = llm

    resp = client.post("/ict/refresh", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ict")
    bar = client.get("/status-bar").text
    assert "ICT refresh running" in bar
    assert "New runs are blocked until it finishes" in bar

    again = client.post("/ict/refresh", follow_redirects=False)
    assert "ICT refresh is already running" in _busy_message(again)

    deadline = time.monotonic() + 10
    while client.app.state.analysis_progress.status == "running":
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert client.app.state.analysis_progress.status == "SUCCESS"


def test_status_bar_shows_refused_start_once(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.cookies.set(BUSY_COOKIE, "A pipeline run is already running.")

    first = client.get("/status-bar")

    assert "Not started" in first.text
    assert "A pipeline run is already running." in first.text
    client.cookies.delete(BUSY_COOKIE)
    assert "Not started" not in client.get("/status-bar").text


def test_scheduled_reconciliation_respects_guard_and_reports_progress(
    tmp_path: Path, monkeypatch
) -> None:
    from unittest.mock import patch

    from regwatch.scheduler.jobs import SchedulerManager
    from regwatch.services.cssf_discovery import CssfDiscoveryService

    client = _client(tmp_path, monkeypatch)
    with client:  # runs the lifespan, which registers the scheduled jobs
        state = client.app.state
        job = state.scheduler_manager._scheduler.get_job(
            SchedulerManager.RECONCILIATION_JOB_ID
        ).func

        state.analysis_progress.start(run_id=0, total=5, task="Catalog refresh")
        with patch.object(CssfDiscoveryService, "run") as run:
            job()
        run.assert_not_called()
        state.analysis_progress.finish("SUCCESS")

        seen: list[str] = []

        def _fake_run(self, **kwargs) -> int:  # type: ignore[no-untyped-def]
            seen.append(state.cssf_discovery_progress.status)
            return 1

        with patch.object(CssfDiscoveryService, "run", _fake_run):
            job()
        assert seen == ["running"]
        assert state.cssf_discovery_progress.status != "running"
