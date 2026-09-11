from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import sessionmaker

from regwatch.config import CssfDiscoveryConfig, PublicationTypeConfig
from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
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


def _seed_default_entity_types(sf) -> None:
    """Seed the entity_type table with AIFM + CHAPTER15_MANCO defaults.

    Inlined here (rather than a shared conftest fixture) because Task 19
    introduces the shared ``seeded_entity_types`` fixture; until then,
    every test that exercises ``CssfDiscoveryService.run()`` must seed.
    """
    from regwatch.db.entity_type_seed import seed_default_entity_types  # noqa: PLC0415
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()


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
    _seed_default_entity_types(sf)
    client = httpx.Client(transport=_mock_transport(), base_url="https://www.cssf.lu")
    run_id = _svc(sf, client=client).run(
        entity_types=["AIFM"], mode="full", triggered_by="USER_CLI",
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
    _seed_default_entity_types(sf)
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
        entity_types=["AIFM"], mode="incremental", triggered_by="USER_CLI",
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
    _seed_default_entity_types(sf)
    first_ref = _first_ref_in_listing()
    with sf() as s:
        s.add(RegulationOverride(
            reference_number=first_ref,
            action="EXCLUDE",
            created_at=datetime.now(UTC),
        ))
        s.commit()
    client = httpx.Client(transport=_mock_transport(), base_url="https://www.cssf.lu")
    run_id = _svc(sf, client=client).run(
        entity_types=["AIFM"], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number=first_ref).one_or_none()
        assert reg is None
        items = s.query(DiscoveryRunItem).filter_by(run_id=run_id).all()
        excluded = [i for i in items if "excluded" in (i.note or "")]
        assert excluded, "expected at least one UNCHANGED-excluded item"


def test_detail_404_marks_existing_regulation_repealed(tmp_path):
    sf = _setup_db(tmp_path)
    _seed_default_entity_types(sf)
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
        entity_types=["AIFM"], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number=first_ref).one()
        assert reg.lifecycle_stage is LifecycleStage.REPEALED


def test_stubs_created_for_unknown_amendment_targets(tmp_path):
    sf = _setup_db(tmp_path)
    _seed_default_entity_types(sf)
    client = httpx.Client(transport=_mock_transport(), base_url="https://www.cssf.lu")
    _svc(sf, client=client).run(
        entity_types=["AIFM"], mode="full", triggered_by="USER_CLI",
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


def test_map_labels_to_slugs(tmp_path):
    """``_map_labels_to_slugs`` matches detail-page labels against the
    DB-built substring map and returns the entity-type slugs."""
    from regwatch.services.cssf_discovery import _map_labels_to_slugs, build_label_map

    sf = _setup_db(tmp_path)
    _seed_default_entity_types(sf)
    with sf() as s:
        label_map = build_label_map(s)

    assert "AIFM" in _map_labels_to_slugs(
        ["Alternative investment fund managers", "Credit institutions"], label_map
    )
    assert "CHAPTER15_MANCO" in _map_labels_to_slugs(
        ["UCITS management companies"], label_map
    )
    result = _map_labels_to_slugs(
        ["Alternative investment fund managers", "UCITS management companies"],
        label_map,
    )
    assert set(result) == {"AIFM", "CHAPTER15_MANCO"}
    # Unrelated labels → empty
    assert _map_labels_to_slugs(
        ["Credit institutions", "Insurance companies"], label_map
    ) == []


def test_all_failed_when_listing_500s(tmp_path):
    sf = _setup_db(tmp_path)
    _seed_default_entity_types(sf)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://www.cssf.lu")
    run_id = _svc(sf, client=client).run(
        entity_types=["AIFM"], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        run = s.get(DiscoveryRun, run_id)
        assert run.status in ("FAILED", "PARTIAL")
        assert run.error_summary is not None


def test_run_reads_filter_ids_from_entity_type_table(tmp_path):
    """Filter IDs come from EntityType.cssf_entity_filter_id, not config."""
    from regwatch.db.entity_type_seed import seed_default_entity_types
    from regwatch.services.entity_types import EntityTypeService

    sf = _setup_db(tmp_path)
    with sf() as s:
        seed_default_entity_types(s)
        # Set AIFM's filter ID to a sentinel and verify the scraper requests it.
        svc = EntityTypeService(s)
        aifm = svc.get_by_slug("AIFM")
        assert aifm is not None
        svc.update(aifm.entity_type_id, cssf_entity_filter_id=99999)
        s.commit()

    seen_entity_filter_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if "entity_type" in params:
            seen_entity_filter_ids.append(params["entity_type"])
        if path in ("/en/regulatory-framework/", "/en/regulatory-framework"):
            return httpx.Response(200, text="<html><body></body></html>")
        if "/en/regulatory-framework/page/" in path:
            return httpx.Response(200, text="<html><body></body></html>")
        return httpx.Response(404)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://www.cssf.lu"
    )
    cfg = CssfDiscoveryConfig(
        request_delay_ms=0,
        publication_types=[
            PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
        ],
        retire_min_scraped=0,
    )
    service = CssfDiscoveryService(session_factory=sf, config=cfg, http_client=client)
    run_id = service.run(entity_types=["AIFM"], mode="full", triggered_by="TEST")
    assert run_id > 0
    # The scraper must have used the DB-stored filter id (99999), not the
    # legacy hard-coded 502.
    assert "99999" in seen_entity_filter_ids, (
        f"expected filter_id=99999 in requests; saw {seen_entity_filter_ids}"
    )
    assert "502" not in seen_entity_filter_ids


def test_run_skips_slugs_without_filter_id(tmp_path, caplog):
    """A slug with cssf_entity_filter_id=NULL is skipped with INFO log."""
    import logging

    from regwatch.db.entity_type_seed import seed_default_entity_types
    from regwatch.services.entity_types import EntityTypeService

    sf = _setup_db(tmp_path)
    with sf() as s:
        seed_default_entity_types(s)
        EntityTypeService(s).create(
            slug="PSF_SPECIALISED",
            label="PSF Specialised",
            cssf_entity_filter_id=None,
        )
        s.commit()

    cfg = CssfDiscoveryConfig(
        request_delay_ms=0,
        publication_types=[
            PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
        ],
        retire_min_scraped=0,
    )
    service = CssfDiscoveryService(session_factory=sf, config=cfg)
    with caplog.at_level(logging.INFO):
        service.run(
            entity_types=["PSF_SPECIALISED"], mode="full", triggered_by="TEST",
        )
    assert any(
        "PSF_SPECIALISED" in r.message and "no CSSF filter ID" in r.message
        for r in caplog.records
    ), f"expected skip-log for PSF_SPECIALISED; saw: {[r.message for r in caplog.records]}"


def _single_row_transport(
    *, row_type: str, title: str, slug: str, detail_h1: str, subtitle: str
) -> httpx.MockTransport:
    listing = f"""<html><body><ul><li class="library-element">
      <p class="library-element__type">{row_type}</p>
      <h3 class="library-element__title"><a href="/en/Document/{slug}/">{title}</a></h3>
      <span class="date--published">Published on 12.03.2026</span>
    </li></ul></body></html>"""
    detail = f"""<html><body>
      <h1 class="single-news__title">{detail_h1}</h1>
      <div class="single-news__subtitle"><p>{subtitle}</p></div>
    </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.rstrip("/") == "/en/regulatory-framework":
            return httpx.Response(200, text=listing)
        if "/en/regulatory-framework/page/" in path:
            return httpx.Response(200, text="<html><body></body></html>")
        if path == f"/en/Document/{slug}/":
            return httpx.Response(200, text=detail)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _svc_for(sf, pub: PublicationTypeConfig, transport: httpx.MockTransport):
    cfg = CssfDiscoveryConfig(
        request_delay_ms=0, publication_types=[pub], retire_min_scraped=0,
    )
    client = httpx.Client(transport=transport, base_url="https://www.cssf.lu")
    return CssfDiscoveryService(session_factory=sf, config=cfg, http_client=client)


def test_annex_gets_its_own_row_and_leaves_parent_circular_alone(tmp_path):
    sf = _setup_db(tmp_path)
    _seed_default_entity_types(sf)
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 22/822",
            title="Circular CSSF 22/822 on the parent topic",
            issuing_authority="CSSF", lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False, url="https://parent.pdf", source_of_truth="CSSF_WEB",
        ))
        s.commit()
    transport = _single_row_transport(
        row_type="Annex to a CSSF circular",
        title="Annex to Circular CSSF 22/822",
        slug="annex-of-circular-cssf-22-822-11",
        detail_h1="Annex to Circular CSSF 22/822",
        subtitle="Reporting template",
    )
    pub = PublicationTypeConfig(
        label="Annex to a CSSF circular", filter_id=5843, type="CSSF_CIRCULAR_ANNEX",
    )
    _svc_for(sf, pub, transport).run(
        entity_types=["AIFM"], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        parent = s.query(Regulation).filter_by(reference_number="CSSF 22/822").one()
        assert parent.title == "Circular CSSF 22/822 on the parent topic"
        assert parent.url == "https://parent.pdf"
        annex = s.query(Regulation).filter_by(
            reference_number="annex-of-circular-cssf-22-822-11"
        ).one()
        assert annex.type is RegulationType.CSSF_CIRCULAR_ANNEX
        assert annex.title == "Annex to Circular CSSF 22/822 – Reporting template"


def test_circular_letter_is_created_with_its_subtitle_in_the_title(tmp_path):
    sf = _setup_db(tmp_path)
    _seed_default_entity_types(sf)
    transport = _single_row_transport(
        row_type="CSSF circular",
        title="Circular letter",
        slug="circular-letter-2026-03-18",
        detail_h1="Circular letter",
        subtitle="Latest update on the AML/CFT standardised data collection",
    )
    pub = PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR")
    _svc_for(sf, pub, transport).run(
        entity_types=["AIFM"], mode="full", triggered_by="USER_CLI",
    )
    with sf() as s:
        reg = s.query(Regulation).filter_by(
            reference_number="circular-letter-2026-03-18"
        ).one()
        assert reg.title == (
            "Circular letter – Latest update on the AML/CFT standardised data collection"
        )


def test_placeholder_row_is_promoted_when_its_listing_row_appears(tmp_path):
    """A stub created for an amending circular becomes a full entry once the
    circular itself is listed -- and doesn't stop an incremental crawl."""
    sf = _setup_db(tmp_path)
    _seed_default_entity_types(sf)
    with sf() as s:
        s.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="circular-letter-2026-03-18",
            title="circular-letter-2026-03-18", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, is_ict=False, needs_review=True,
            url="", source_of_truth="CSSF_STUB",
        ))
        s.commit()
    transport = _single_row_transport(
        row_type="CSSF circular",
        title="Circular letter",
        slug="circular-letter-2026-03-18",
        detail_h1="Circular letter",
        subtitle="Latest update on the AML/CFT standardised data collection",
    )
    pub = PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR")
    _svc_for(sf, pub, transport).run(
        entity_types=["AIFM"], mode="incremental", triggered_by="USER_CLI",
    )
    with sf() as s:
        reg = s.query(Regulation).filter_by(
            reference_number="circular-letter-2026-03-18"
        ).one()
        assert reg.source_of_truth == "CSSF_WEB"
        assert reg.title.startswith("Circular letter – ")
        assert reg.publication_date is not None
        assert [a.authorization_type for a in reg.applicabilities] == ["AIFM"]
