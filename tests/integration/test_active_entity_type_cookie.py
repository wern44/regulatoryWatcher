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


def test_sidebar_shows_global_switcher(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/catalog")
    assert r.status_code == 200
    # The switcher form posts to the cookie route.
    assert 'action="/settings/active-entity-type"' in r.text
    # "All entity types" is the default option.
    assert "All entity types" in r.text
    # Hardcoded sidebar links are gone.
    assert '/catalog?authorization=AIFM"' not in r.text
    assert '/catalog?authorization=CHAPTER15_MANCO"' not in r.text


def test_sidebar_marks_active_option_when_cookie_set(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.cookies.set("active_entity_type", "AIFM")
        r = client.get("/catalog")
    assert 'value="AIFM" selected' in r.text or 'selected value="AIFM"' in r.text


def test_catalog_dropdown_renders_from_db(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        # Add a third entity type — it should appear in the dropdown.
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            EntityTypeService(s).create(slug="PSF_SPECIALISED", label="PSF Specialised")
            s.commit()
        r = client.get("/catalog")
    assert r.status_code == 200
    assert "PSF Specialised" in r.text


def test_catalog_cookie_filters_when_no_query_param(tmp_path, monkeypatch):
    """A bare /catalog?... visit (with a non-empty query, no authorization) uses the cookie."""
    with _client(tmp_path, monkeypatch) as client:
        # Seed a regulation for AIFM only.
        with client.app.state.session_factory() as s:
            from regwatch.db.models import (
                LifecycleStage, Regulation, RegulationApplicability, RegulationType,
            )
            reg = Regulation(
                reference_number="AIFM-ONLY",
                type=RegulationType.CSSF_CIRCULAR,
                title="AIFM only",
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                url="http://x",
                source_of_truth="SEED",
            )
            s.add(reg)
            s.flush()
            s.add(RegulationApplicability(
                regulation_id=reg.regulation_id, authorization_type="AIFM"
            ))
            s.commit()
        client.cookies.set("active_entity_type", "CHAPTER15_MANCO")
        # No "authorization=" in URL but cookie set: AIFM-only reg should be hidden.
        r = client.get("/catalog?lifecycle=IN_FORCE")
    assert "AIFM-ONLY" not in r.text
