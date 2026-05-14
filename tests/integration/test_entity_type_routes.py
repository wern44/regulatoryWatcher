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


def test_add_entity_type_happy_path(tmp_path, monkeypatch):
    # NOTE: spec originally opened two `_client(tmp_path, ...)` blocks, but the
    # `_client` helper unconditionally calls `(tmp_path / "pdfs").mkdir()` (no
    # exist_ok=True), which raises FileExistsError on the second invocation.
    # The intent is "data persists in the sqlite file"; a single `_client` and
    # a fresh session via the same factory exercises that just as well — both
    # the POST and the verifying session use the on-disk `tmp_path / "app.db"`.
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/settings/entity-types",
            data={
                "slug": "PSF_SPECIALISED",
                "label": "PSF Specialised",
                "cssf_entity_filter_id": "1234",
                "sort_order": "30",
                "cssf_detail_labels": "Specialised PSF, PSF spécialisé",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/settings/entity-types"

        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            row = EntityTypeService(s).get_by_slug("PSF_SPECIALISED")
        assert row is not None
        assert row.cssf_entity_filter_id == 1234
        assert row.cssf_detail_labels == ["Specialised PSF", "PSF spécialisé"]


def test_add_rejects_bad_slug(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/settings/entity-types",
            data={"slug": "lower_case", "label": "x"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "slug-invalid" in r.headers["location"]


def test_add_rejects_duplicate_slug(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/settings/entity-types",
            data={"slug": "AIFM", "label": "duplicate"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "slug-conflict" in r.headers["location"]


def test_add_refreshes_app_state_prompt_cache(tmp_path, monkeypatch):
    """After adding a type, app.state.entity_type_prompt reflects it."""
    with _client(tmp_path, monkeypatch) as client:
        client.post(
            "/settings/entity-types",
            data={"slug": "PSF_SUPPORT", "label": "PSF Support"},
            follow_redirects=False,
        )
        assert "PSF_SUPPORT" in client.app.state.entity_type_prompt


def test_deactivate_hides_from_active_list(tmp_path, monkeypatch):
    # NOTE: same `_client` constraint as test_add_entity_type_happy_path — the
    # helper calls `(tmp_path / "pdfs").mkdir()` without exist_ok=True, so we
    # use a single `_client` block. Each `session_factory()` call still gets a
    # fresh DBAPI connection (NullPool), so we can verify persistence by
    # re-opening a session after the POST against the same on-disk db.
    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            aifm_id = EntityTypeService(s).get_by_slug("AIFM").entity_type_id
        r = client.post(
            f"/settings/entity-types/{aifm_id}/deactivate",
            follow_redirects=False,
        )
        assert r.status_code == 303

        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            active = [r.slug for r in EntityTypeService(s).list_active()]
        assert "AIFM" not in active


def test_reactivate(tmp_path, monkeypatch):
    # NOTE: see test_deactivate_hides_from_active_list — single `_client` block
    # is required because the helper's `mkdir()` call has no exist_ok=True.
    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            svc = EntityTypeService(s)
            aifm_id = svc.get_by_slug("AIFM").entity_type_id
            svc.deactivate(aifm_id)
            s.commit()
        r = client.post(
            f"/settings/entity-types/{aifm_id}/reactivate",
            follow_redirects=False,
        )
        assert r.status_code == 303

        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            active = [r.slug for r in EntityTypeService(s).list_active()]
        assert "AIFM" in active
