"""Integration tests for the CSSF discovery web routes."""
from __future__ import annotations

import time
from pathlib import Path

from regwatch.db.models import DiscoveryRun, Regulation
from tests.integration.test_app_smoke import _client

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cssf"


def test_run_page_renders_not_found(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/discovery/runs/9999")
    assert r.status_code == 200
    assert "not found" in r.text.lower()


def test_run_list_page_empty(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/discovery/runs")
    assert r.status_code == 200
    assert "no discovery runs" in r.text.lower() or "discovery runs" in r.text.lower()


def test_catalog_has_discover_button(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/catalog")
    assert r.status_code == 200
    assert 'action="/catalog/discover-cssf"' in r.text
    assert "Discover from CSSF" in r.text


def test_post_discover_cssf_spawns_run(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)

    from datetime import date as _date

    from regwatch.discovery.cssf_scraper import CircularDetail, CircularListingRow

    def _fake_list(*, entity_filter_id, content_type_filter_id, publication_type_label, **kwargs):
        yield CircularListingRow(
            reference_number="CSSF 22/806",
            raw_title="Circular CSSF 22/806 on outsourcing",
            description="Outsourcing arrangements.",
            publication_date=_date(2022, 4, 22),
            detail_url="https://www.cssf.lu/en/Document/circular-cssf-22-806/",
        )

    def _fake_detail(url, *args, **kwargs):
        return CircularDetail(
            reference_number="CSSF 22/806",
            clean_title="on outsourcing arrangements",
            amended_by_refs=["CSSF 25/883"],
            amends_refs=[],
            supersedes_refs=[],
            applicable_entities=["Alternative investment fund managers"],
            pdf_url_en="https://example.test/cssf22_806eng.pdf",
            pdf_url_fr=None,
            published_at=_date(2022, 4, 22),
            updated_at=None,
            description="Outsourcing arrangements.",
        )

    # Patch the names used *inside* the service module (they were imported
    # by name, so monkeypatching the scraper module is not enough).
    import regwatch.services.cssf_discovery as svc_mod
    monkeypatch.setattr(svc_mod, "list_circulars", _fake_list)
    monkeypatch.setattr(svc_mod, "fetch_circular_detail", _fake_detail)

    r = c.post(
        "/catalog/discover-cssf",
        data={"mode": "full", "entity_types": ["AIFM"]},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    loc = r.headers["location"]
    assert "/discovery/runs/" in loc

    # Poll for worker completion. Open a fresh session each iteration so we
    # don't sit inside a single SQLAlchemy transaction that caches the first
    # read (the worker writes happen in a separate thread/session).
    sf = c.app.state.session_factory
    run_id: int | None = None
    final_status: str | None = None
    final_new_count = 0
    for _ in range(100):
        with sf() as s:
            run = s.query(DiscoveryRun).order_by(DiscoveryRun.run_id.desc()).first()
            if run and run.status != "RUNNING":
                run_id = run.run_id
                final_status = run.status
                final_new_count = run.new_count
                break
        time.sleep(0.1)
    else:
        raise AssertionError("discovery worker did not complete within 10s")

    assert final_status in ("SUCCESS", "PARTIAL")
    assert final_new_count >= 1
    with sf() as s:
        assert s.query(Regulation).filter_by(reference_number="CSSF 22/806").count() == 1

    # Run page renders with results.
    r2 = c.get(f"/discovery/runs/{run_id}")
    assert r2.status_code == 200
    assert "CSSF 22/806" in r2.text

    # Runs list renders with this run.
    r3 = c.get("/discovery/runs")
    assert r3.status_code == 200
    assert f"#{run_id}" in r3.text
