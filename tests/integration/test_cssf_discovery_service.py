from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import sessionmaker

from regwatch.config import CssfDiscoveryConfig, PublicationTypeConfig
from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    AuthorizationType,
    Base,
    DiscoveryRun,
    DiscoveryRunItem,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationOverride,
    RegulationType,
)
from regwatch.services.cssf_discovery import CssfDiscoveryService

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cssf"
LISTING_HTML = (FIXTURES / "listing_aifms_page1.html").read_text(encoding="utf-8")
DETAIL_22_806 = (FIXTURES / "detail_22_806.html").read_text(encoding="utf-8")


def _setup_db(tmp_path):
    engine = create_app_engine(tmp_path / "app.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _mock_transport(listing_body=LISTING_HTML, detail_body=DETAIL_22_806):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in ("/en/regulatory-framework/", "/en/regulatory-framework"):
            return httpx.Response(200, text=listing_body)
        if "/en/regulatory-framework/page/" in path:
            return httpx.Response(200, text="<html><body></body></html>")
        if "/en/Document/" in path:
            return httpx.Response(200, text=detail_body)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def _svc(sf, *, client=None):
    cfg = CssfDiscoveryConfig(
        request_delay_ms=0,
        publication_types=[
            PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
        ],
        retire_min_scraped=0,  # disable floor in tests that use tiny synthetic data
    )
    return CssfDiscoveryService(session_factory=sf, config=cfg, http_client=client)


def _first_ref_in_listing() -> str:
    import re
    m = re.search(r"CSSF\s*\d+/\d+", LISTING_HTML)
    assert m, "listing fixture must contain at least one CSSF ref"
    # Normalize spacing to match whatever the scraper produces ("CSSF 26/909")
    return m.group(0).replace("CSSF", "CSSF ").replace("  ", " ").strip()


def test_full_crawl_creates_new_rows_and_applicability(tmp_path):
    sf = _setup_db(tmp_path)
    client = httpx.Client(transport=_mock_transport(), base_url="https://www.cssf.lu")
    run_id = _svc(sf, client=client).run(
        entity_types=[AuthorizationType.AIFM], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status in ("SUCCESS", "PARTIAL")
        assert run.new_count > 0
        new_items = s.query(DiscoveryRunItem).filter_by(run_id=run_id, outcome="NEW").all()
        assert new_items
        for item in new_items:
            assert item.regulation_id is not None
            reg = s.get(Regulation, item.regulation_id)
            assert reg.source_of_truth in ("CSSF_WEB", "CSSF_STUB")
        appls = s.query(RegulationApplicability).all()
        assert any(a.authorization_type == "AIFM" for a in appls)


def test_incremental_stops_at_first_known_ref(tmp_path):
    sf = _setup_db(tmp_path)
    first_ref = _first_ref_in_listing()
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number=first_ref, title="seeded",
            issuing_authority="CSSF", lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False, url="", source_of_truth="SEED",
        ))
        s.commit()
    client = httpx.Client(transport=_mock_transport(), base_url="https://www.cssf.lu")
    run_id = _svc(sf, client=client).run(
        entity_types=[AuthorizationType.AIFM], mode="incremental", triggered_by="USER_CLI",
    )
    with sf() as s:
        run = s.get(DiscoveryRun, run_id)
        # The first listing row matches an existing ref → walk stops silently
        assert run.new_count == 0
        # No item rows for the stopped walk
        items = s.query(DiscoveryRunItem).filter_by(run_id=run_id).all()
        assert run.total_scraped == len(items)


def test_override_exclude_skips_regulation(tmp_path):
    sf = _setup_db(tmp_path)
    with sf() as s:
        s.add(RegulationOverride(
            reference_number="CSSF 22/806",
            action="EXCLUDE",
            created_at=datetime.now(UTC),
        ))
        s.commit()
    client = httpx.Client(transport=_mock_transport(), base_url="https://www.cssf.lu")
    run_id = _svc(sf, client=client).run(
        entity_types=[AuthorizationType.AIFM], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number="CSSF 22/806").one_or_none()
        assert reg is None
        items = s.query(DiscoveryRunItem).filter_by(run_id=run_id).all()
        excluded = [i for i in items if "excluded" in (i.note or "")]
        assert excluded, "expected at least one UNCHANGED-excluded item"


def test_detail_404_marks_existing_regulation_repealed(tmp_path):
    sf = _setup_db(tmp_path)
    first_ref = _first_ref_in_listing()
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number=first_ref, title="old",
            issuing_authority="CSSF", lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False, url="", source_of_truth="CSSF_WEB",
        ))
        s.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in ("/en/regulatory-framework/", "/en/regulatory-framework"):
            return httpx.Response(200, text=LISTING_HTML)
        if "/en/regulatory-framework/page/" in path:
            return httpx.Response(200, text="<html><body></body></html>")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://www.cssf.lu")
    _svc(sf, client=client).run(
        entity_types=[AuthorizationType.AIFM], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number=first_ref).one()
        assert reg.lifecycle_stage is LifecycleStage.REPEALED


def test_stubs_created_for_unknown_amendment_targets(tmp_path):
    sf = _setup_db(tmp_path)
    client = httpx.Client(transport=_mock_transport(), base_url="https://www.cssf.lu")
    _svc(sf, client=client).run(
        entity_types=[AuthorizationType.AIFM], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        stubs = s.query(Regulation).filter_by(source_of_truth="CSSF_STUB").all()
        # The detail fixture (22/806) has "(as amended by CSSF 25/883)" → stub expected
        assert stubs, "expected stub rows for amendment targets"


def test_backfill_updates_titles_and_tags_ict(tmp_path):
    sf = _setup_db(tmp_path)
    # Pre-seed a CSSF_WEB regulation with a bare title + no ICT flag.
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 22/806",
            title="Circular CSSF 22/806",  # bare ref only
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            needs_review=True,
            url="https://example.test/",
            source_of_truth="CSSF_WEB",
        ))
        s.commit()

    client = httpx.Client(
        transport=_mock_transport(), base_url="https://www.cssf.lu",
    )
    counts = _svc(sf, client=client).backfill_titles_and_descriptions()
    assert counts["updated"] >= 1
    # "outsourcing" is in the ICT keyword list, so the heuristic should trip.
    assert counts["newly_ict"] >= 1

    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number="CSSF 22/806").one()
        assert "outsourcing" in reg.title.lower()
        assert reg.is_ict is True
        assert reg.needs_review is False

    # Idempotency: a second pass should not double-update the title.
    counts2 = _svc(sf, client=client).backfill_titles_and_descriptions()
    assert counts2["updated"] == 0
    assert counts2["newly_ict"] == 0


def test_backfill_skips_rows_with_unparseable_reference(tmp_path):
    sf = _setup_db(tmp_path)
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="NOT A REAL REF",
            title="Bare",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            needs_review=True,
            url="",
            source_of_truth="CSSF_WEB",
        ))
        s.commit()

    client = httpx.Client(
        transport=_mock_transport(), base_url="https://www.cssf.lu",
    )
    counts = _svc(sf, client=client).backfill_titles_and_descriptions()
    assert counts["no_url"] == 1
    assert counts["updated"] == 0


def test_reclassify_flips_false_positive(tmp_path):
    sf = _setup_db(tmp_path)
    with sf() as s:
        # Pre-insert a CSSF_WEB row flagged is_ict=True under the OLD heuristic
        # that the new word-boundary heuristic rejects ("ict" in "jurisdictions").
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 22/822",
            title="Circular CSSF 22/822 FATF statements concerning high-risk jurisdictions",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=True,  # stale flag from the old substring heuristic
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        ))
        s.commit()

    counts = _svc(sf).reclassify_cssf_web_ict()
    assert counts["set_false"] == 1

    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number="CSSF 22/822").one()
        assert reg.is_ict is False
        assert reg.needs_review is True  # routed to LLM classify


def test_reclassify_respects_override(tmp_path):
    sf = _setup_db(tmp_path)
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 22/822",
            title="FATF statements concerning high-risk jurisdictions",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=True,
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        ))
        s.add(RegulationOverride(
            reference_number="CSSF 22/822",
            action="SET_ICT",
            created_at=datetime.now(UTC),
        ))
        s.commit()

    counts = _svc(sf).reclassify_cssf_web_ict()
    assert counts["skipped_override"] == 1
    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number="CSSF 22/822").one()
        assert reg.is_ict is True  # override respected


def test_map_labels_to_auth_types():
    from regwatch.services.cssf_discovery import _map_labels_to_auth_types
    assert AuthorizationType.AIFM in _map_labels_to_auth_types(
        ["Alternative investment fund managers", "Credit institutions"]
    )
    assert AuthorizationType.CHAPTER15_MANCO in _map_labels_to_auth_types(
        ["UCITS management companies"]
    )
    result = _map_labels_to_auth_types(
        ["Alternative investment fund managers", "UCITS management companies"]
    )
    assert set(result) == {AuthorizationType.AIFM, AuthorizationType.CHAPTER15_MANCO}
    # Unrelated labels → empty
    assert _map_labels_to_auth_types(["Credit institutions", "Insurance companies"]) == []


def test_all_failed_when_listing_500s(tmp_path):
    sf = _setup_db(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://www.cssf.lu")
    run_id = _svc(sf, client=client).run(
        entity_types=[AuthorizationType.AIFM], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status in ("FAILED", "PARTIAL")
        assert run.error_summary is not None
