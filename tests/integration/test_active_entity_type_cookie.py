"""Render context injects entity_types and active_entity_type."""
from __future__ import annotations

from tests.integration.test_app_smoke import _client


def test_render_page_injects_entity_types(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/catalog")
    assert r.status_code in (200, 303)
    if r.status_code == 200:
        # The sidebar's data-driven switcher shows both seeded slugs.
        assert "AIFM" in r.text
        assert "CHAPTER15_MANCO" in r.text


def test_active_entity_type_cookie_filters_catalog(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        # Without cookie or query param -> "All": catalog renders without filter.
        r1 = client.get("/catalog")
        assert r1.status_code == 200

        # Set the cookie via the dedicated route.
        r2 = client.post(
            "/settings/active-entity-type",
            data={"entity_type": "AIFM"},
            follow_redirects=False,
        )
        assert r2.status_code == 303
        assert "active_entity_type=AIFM" in r2.headers.get("set-cookie", "")
