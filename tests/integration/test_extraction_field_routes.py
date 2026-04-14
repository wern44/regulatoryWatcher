from pathlib import Path

from tests.integration.test_app_smoke import _client


def test_extraction_fields_page_lists_core_rows(tmp_path: Path, monkeypatch) -> None:
    c = _client(tmp_path, monkeypatch)
    r = c.get("/settings/extraction")
    assert r.status_code == 200
    assert "Main Points" in r.text
    assert "ICT / DORA Related" in r.text


def test_create_and_delete_custom_field(tmp_path: Path, monkeypatch) -> None:
    c = _client(tmp_path, monkeypatch)
    r = c.post(
        "/settings/extraction",
        data={
            "name": "severity",
            "label": "Severity",
            "description": "How bad",
            "data_type": "TEXT",
            "enum_values": "",
            "display_order": "200",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    listing = c.get("/settings/extraction").text
    assert "Severity" in listing

    from regwatch.services.extraction_fields import ExtractionFieldService
    with c.app.state.session_factory() as s:
        fid = next(
            f.field_id
            for f in ExtractionFieldService(s).list()
            if f.name == "severity"
        )
    r = c.post(f"/settings/extraction/{fid}/delete", follow_redirects=False)
    assert r.status_code in (302, 303)
    listing = c.get("/settings/extraction").text
    assert "Severity" not in listing


def test_cannot_delete_core_field(tmp_path: Path, monkeypatch) -> None:
    c = _client(tmp_path, monkeypatch)
    from regwatch.services.extraction_fields import ExtractionFieldService
    with c.app.state.session_factory() as s:
        core_id = next(
            f.field_id for f in ExtractionFieldService(s).list() if f.is_core
        )
    r = c.post(f"/settings/extraction/{core_id}/delete", follow_redirects=False)
    assert r.status_code == 400
