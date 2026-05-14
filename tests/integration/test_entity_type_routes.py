"""HTTP route tests for the Entity Types Settings page."""
from __future__ import annotations

from tests.integration.test_app_smoke import _client


def test_listing_page_renders(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/settings/entity-types")
    assert r.status_code == 200
    assert "Entity Types" in r.text
    # Both seeded rows are visible.
    assert "AIFM" in r.text
    assert "CHAPTER15_MANCO" in r.text
    # The add form is reachable.
    assert "Add entity type" in r.text


def test_listing_page_separates_active_from_hidden(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            svc = EntityTypeService(s)
            aifm = svc.get_by_slug("AIFM")
            svc.deactivate(aifm.entity_type_id)
            s.commit()
        r = client.get("/settings/entity-types")
    assert r.status_code == 200
    assert "Hidden" in r.text  # the hidden-rows heading appears
