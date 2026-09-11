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


def test_schedules_page_renders(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/settings/schedules")
    assert resp.status_code == 200
    assert "Scheduled Processes" in resp.text
    assert "Pipeline Run" in resp.text
    assert "CSSF Discovery" in resp.text
    assert "Full Reconciliation" in resp.text
    assert "Catalog Refresh" in resp.text


def test_save_schedule_for_pipeline(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/schedules/save",
        data={
            "job": "pipeline",
            "enabled": "true",
            "frequency": "daily",
            "time": "07:00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings/schedules"


def test_save_schedule_for_analysis(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/schedules/save",
        data={
            "job": "analysis",
            "frequency": "monthly",
            "time": "04:00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_invalid_schedule_is_rejected_and_not_saved(tmp_path: Path, monkeypatch) -> None:
    """A stored invalid time or frequency made the app fail at the next
    start: the lifespan builds every trigger from the saved values."""
    from regwatch.services.settings import SettingsService

    client = _client(tmp_path, monkeypatch)
    for frequency, time in [("daily", "25:00"), ("daily", "7am"), ("hourly", "07:00")]:
        resp = client.post(
            "/settings/schedules/save",
            data={"job": "pipeline", "enabled": "true", "frequency": frequency, "time": time},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]
    with client.app.state.session_factory() as s:
        assert SettingsService(s).get("scheduler_time") is None
        assert SettingsService(s).get("scheduler_frequency") is None
    assert "Invalid schedule" in client.get(resp.headers["location"]).text


def test_app_starts_with_an_invalid_stored_schedule(tmp_path: Path, monkeypatch) -> None:
    from regwatch.scheduler.jobs import SchedulerManager
    from regwatch.services.settings import SettingsService

    client = _client(tmp_path, monkeypatch)
    with client.app.state.session_factory() as s:
        SettingsService(s).set("scheduler_time", "25:61")
        SettingsService(s).set("scheduler_frequency", "hourly")
        s.commit()

    with client:  # runs the lifespan
        manager = client.app.state.scheduler_manager
        assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is not None
