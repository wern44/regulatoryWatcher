"""Integration tests for the global /status-bar fragment.

These tests verify that every long-running background process that calls an
LLM is surfaced in the status bar with an Abort button.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from fastapi.testclient import TestClient


def _build_config(tmp_path: Path) -> Path:
    shutil.copy("config.example.yaml", tmp_path / "config.yaml")
    cfg_path = tmp_path / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["paths"]["db_file"] = str(tmp_path / "app.db")
    data["paths"]["pdf_archive"] = str(tmp_path / "pdfs")
    data["paths"]["uploads_dir"] = str(tmp_path / "uploads")
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "uploads").mkdir()
    cfg_path.write_text(yaml.safe_dump(data))
    return cfg_path


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    cfg_path = _build_config(tmp_path)
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))
    import importlib

    import regwatch.main as main_module

    importlib.reload(main_module)
    app = main_module.create_app()
    app.state.llm_client.chat_model = "test-model"
    return TestClient(app)


def test_status_bar_shows_analysis_running(tmp_path: Path, monkeypatch) -> None:
    """When analysis_progress.status == 'running', the status bar should mention it."""
    client = _client(tmp_path, monkeypatch)
    client.app.state.analysis_progress.start(run_id=42, total=10)
    client.app.state.analysis_progress.tick(3, 10, "Classifying CSSF 18/698")

    resp = client.get("/status-bar")
    assert resp.status_code == 200
    body = resp.text
    assert "Analysis running" in body or "Catalog refresh running" in body
    # Includes the current item label so the user can see what's happening.
    assert "CSSF 18/698" in body


def test_status_bar_shows_abort_button_for_analysis(
    tmp_path: Path, monkeypatch
) -> None:
    """A running analysis must be abortable from the status bar."""
    client = _client(tmp_path, monkeypatch)
    client.app.state.analysis_progress.start(run_id=42, total=10)

    resp = client.get("/status-bar")
    assert resp.status_code == 200
    assert "/analysis/abort" in resp.text


def test_analysis_abort_endpoint_requests_cancel(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    progress = client.app.state.analysis_progress
    progress.start(run_id=42, total=10)
    assert progress.is_cancel_requested is False

    resp = client.post("/analysis/abort")
    assert resp.status_code in (200, 303)
    assert progress.is_cancel_requested is True


def test_status_bar_idle_when_nothing_running(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/status-bar")
    assert resp.status_code == 200
    # Idle state — no banner text.
    assert "Pipeline running" not in resp.text
    assert "Analysis running" not in resp.text
    assert "Catalog refresh running" not in resp.text
    assert "CSSF Reconciliation running" not in resp.text
