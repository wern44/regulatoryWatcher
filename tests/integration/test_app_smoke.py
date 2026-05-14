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
    # Force a fresh app that picks up the env var.
    import importlib

    import regwatch.main as main_module

    importlib.reload(main_module)
    app = main_module.create_app()
    # Provide a dummy model so FirstStartupMiddleware does not redirect test requests.
    app.state.llm_client.chat_model = "test-model"
    return TestClient(app)


def test_root_returns_dashboard(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/")
    assert response.status_code == 200
    assert "RegWatch" in response.text
    assert "Dashboard" in response.text


def test_settings_page_shows_full_reconciliation_button(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.text
    assert "CSSF catalog reconciliation" in body
    assert "Run full CSSF reconciliation" in body
    assert 'action="/catalog/discover-cssf"' in body
    assert 'name="mode" value="full"' in body


def test_first_startup_redirect_does_not_fire_on_htmx_requests(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: when no chat_model is set, FirstStartupMiddleware redirects
    every non-/static non-/settings request to /settings/setup. Plain browser
    navigation should still redirect, but htmx polls (e.g. /status-bar fires
    on every page load) must NOT — htmx would follow the 307, swap the entire
    /settings/setup page into the tiny status-bar fragment slot, and the
    injected page's own status-bar poll would loop, stacking sidebars.
    """
    client = _client(tmp_path, monkeypatch)
    # Simulate the production "no LLM configured" state.
    client.app.state.llm_client.chat_model = ""

    plain = client.get("/status-bar", follow_redirects=False)
    assert plain.status_code == 307
    assert plain.headers["location"] == "/settings/setup"

    htmx = client.get(
        "/status-bar",
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert htmx.status_code == 200
    assert htmx.text == ""


def test_app_startup_seeds_entity_types(tmp_path, monkeypatch):
    """create_app() populates the entity_type table on first boot."""
    from sqlalchemy import select

    from regwatch.db.models import EntityType
    client_ctx = _client(tmp_path, monkeypatch)
    with client_ctx as client:
        with client.app.state.session_factory() as s:
            slugs = sorted(s.scalars(select(EntityType.slug)).all())
        assert "AIFM" in slugs
        assert "CHAPTER15_MANCO" in slugs
