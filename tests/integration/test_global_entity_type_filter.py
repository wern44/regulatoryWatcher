"""The sidebar `active_entity_type` cookie is the single source of truth.

These tests cover the bug fix where:
- the cookie now persists when navigating Catalog -> Inbox -> Catalog
  (the catalog route used to clobber it via its `catalog_filters` cookie)
- Dashboard, Drafts, Deadlines, and ICT all respect the cookie
- the info pill renders on every filtered page
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed_two_entity_types(db_file: Path) -> None:
    """Seed one AIFM-only reg and one MANCO-only reg, both IN_FORCE + ICT."""
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for ref, slug in [("AIFM-ICT-01", "AIFM"), ("MANCO-ICT-01", "CHAPTER15_MANCO")]:
            reg = Regulation(
                type=RegulationType.CSSF_CIRCULAR,
                reference_number=ref,
                title=ref,
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=True,
                source_of_truth="SEED",
                url="https://example.com",
            )
            reg.applicabilities.append(
                RegulationApplicability(authorization_type=slug)
            )
            session.add(reg)
        # A draft for the same two slugs
        for ref, slug in [
            ("AIFM-DRAFT-01", "AIFM"),
            ("MANCO-DRAFT-01", "CHAPTER15_MANCO"),
        ]:
            reg = Regulation(
                type=RegulationType.CSSF_CIRCULAR,
                reference_number=ref,
                title=ref,
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.CONSULTATION,
                is_ict=False,
                source_of_truth="SEED",
                url="https://example.com",
            )
            reg.applicabilities.append(
                RegulationApplicability(authorization_type=slug)
            )
            session.add(reg)
        session.commit()


def test_cookie_persists_when_navigating_catalog_inbox_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: catalog used to clobber the active_entity_type cookie via
    its `catalog_filters` redirect. Verify it no longer does."""
    client = _client(tmp_path, monkeypatch)
    _seed_two_entity_types(tmp_path / "app.db")

    # User sets sidebar to AIFM.
    client.cookies.set("active_entity_type", "AIFM")

    # Visit catalog with some non-entity filter; it persists into catalog_filters.
    r1 = client.get("/catalog?lifecycle=IN_FORCE&ict=&search=")
    assert r1.status_code == 200
    assert client.cookies.get("active_entity_type") == "AIFM"

    # Navigate to inbox.
    r2 = client.get("/inbox")
    assert r2.status_code == 200
    assert client.cookies.get("active_entity_type") == "AIFM"

    # Back to /catalog with no query string -> 303 redirect to catalog_filters.
    r3 = client.get("/catalog")
    assert r3.status_code == 200  # TestClient follows redirects by default
    # The cookie must not have been deleted by the catalog route.
    assert client.cookies.get("active_entity_type") == "AIFM"


def test_ict_page_filters_by_sidebar_cookie(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_two_entity_types(tmp_path / "app.db")

    # Without a cookie -> both ICT regs visible.
    r = client.get("/ict")
    assert r.status_code == 200
    assert "AIFM-ICT-01" in r.text
    assert "MANCO-ICT-01" in r.text

    # With AIFM cookie -> only AIFM-ICT-01 visible.
    client.cookies.set("active_entity_type", "AIFM")
    r = client.get("/ict")
    assert r.status_code == 200
    assert "AIFM-ICT-01" in r.text
    assert "MANCO-ICT-01" not in r.text


def test_ict_page_shows_entity_type_pill(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_two_entity_types(tmp_path / "app.db")

    # No cookie -> "Showing all entity types"
    r = client.get("/ict")
    assert "Showing all entity types" in r.text

    # With cookie -> "Filtered by entity type:" + label
    client.cookies.set("active_entity_type", "AIFM")
    r = client.get("/ict")
    assert "Filtered by entity type:" in r.text
    assert "AIFM" in r.text


def test_drafts_page_filters_by_sidebar_cookie(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_two_entity_types(tmp_path / "app.db")

    client.cookies.set("active_entity_type", "AIFM")
    r = client.get("/drafts")
    assert r.status_code == 200
    assert "AIFM-DRAFT-01" in r.text
    assert "MANCO-DRAFT-01" not in r.text


def test_dashboard_kpis_respect_sidebar_cookie(tmp_path: Path, monkeypatch) -> None:
    """KPIs (catalog count + ICT count) drop to the AIFM-only subset."""
    client = _client(tmp_path, monkeypatch)
    _seed_two_entity_types(tmp_path / "app.db")

    # Without cookie -> both regs counted in catalog (2 IN_FORCE) and ICT (2 ICT).
    r_all = client.get("/")
    assert r_all.status_code == 200
    assert 'data-kpi="catalog">2<' in r_all.text
    assert 'data-kpi="ict">2<' in r_all.text

    # With AIFM cookie -> only 1 of each.
    client.cookies.set("active_entity_type", "AIFM")
    r_aifm = client.get("/")
    assert r_aifm.status_code == 200
    assert 'data-kpi="catalog">1<' in r_aifm.text
    assert 'data-kpi="ict">1<' in r_aifm.text


def test_catalog_no_longer_renders_authorization_dropdown(
    tmp_path: Path, monkeypatch
) -> None:
    """The per-page entity dropdown is gone — sidebar is the single source."""
    client = _client(tmp_path, monkeypatch)
    r = client.get("/catalog")
    assert r.status_code == 200
    assert 'name="authorization"' not in r.text
    assert "Any authorisation" not in r.text
    # The lifecycle/ICT filters stay.
    assert 'name="lifecycle"' in r.text
    assert 'name="ict"' in r.text


def test_inbox_no_longer_renders_entity_type_dropdown(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    r = client.get("/inbox")
    assert r.status_code == 200
    # The "All (relevant)" inbox-page dropdown option is gone (sidebar form
    # also uses `name="entity_type"`, so that string alone is not specific).
    assert "All (relevant)" not in r.text
    # The source filter stays.
    assert 'name="source"' in r.text
