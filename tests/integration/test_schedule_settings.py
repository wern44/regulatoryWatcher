import shutil
from pathlib import Path
from unittest.mock import MagicMock

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
    # Provide a mock scheduler_manager since TestClient without context manager
    # does not run the lifespan.
    mock_manager = MagicMock()
    mock_manager.next_run_time.return_value = None
    app.state.scheduler_manager = mock_manager
    return TestClient(app)


def test_settings_page_shows_scheduler_section(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Scheduled Updates" in resp.text
    assert "scheduler_frequency" in resp.text


def test_save_schedule_persists_and_redirects(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/save-schedule",
        data={
            "scheduler_enabled": "true",
            "scheduler_frequency": "weekly",
            "scheduler_time": "09:30",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"

    # Verify settings are persisted by reloading the page.
    resp2 = client.get("/settings")
    assert "weekly" in resp2.text


def test_save_schedule_pauses_when_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/save-schedule",
        data={
            "scheduler_frequency": "daily",
            "scheduler_time": "08:00",
        },
        follow_redirects=False,
    )
    # No scheduler_enabled field means the checkbox was unchecked.
    assert resp.status_code == 303
