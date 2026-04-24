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
