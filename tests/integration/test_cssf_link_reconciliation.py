"""A full discovery run rebuilds the amendment graph from what it crawled."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from regwatch.config import CssfDiscoveryConfig, PublicationTypeConfig
from regwatch.db.engine import create_app_engine
from regwatch.db.entity_type_seed import seed_default_entity_types
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationLifecycleLink,
    RegulationType,
)
from regwatch.services.cssf_discovery import CssfDiscoveryService

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cssf"
DETAIL_22_806 = (FIXTURES / "detail_22_806.html").read_text(encoding="utf-8")

DETAIL_22_805 = """<html><body>
  <h1 class="single-news__title">Circular CSSF 22/805</h1>
  <div class="single-news__subtitle"><p>Revised EBA Guidelines on outsourcing
  arrangements - Publication of Circular CSSF 22/806 on outsourcing
  arrangements</p></div>
  <div class="related-documents-container"><ul>
    <li class="related-document"><h4 class="related-document-title">
      <a href="/en/Document/circular-cssf-22-806/">Circular CSSF 22/806</a></h4>
      <div class="related-document-excerpt">on outsourcing arrangements</div></li>
  </ul></div>
</body></html>"""


def _row(ref: str, slug: str) -> str:
    return f"""<li class="library-element">
      <p class="library-element__type">CSSF circular</p>
      <h3 class="library-element__title"><a href="/en/Document/{slug}/">Circular {ref}</a></h3>
    </li>"""


def _transport() -> httpx.MockTransport:
    listing = (
        "<html><body><ul>"
        + _row("CSSF 22/806", "circular-cssf-22-806")
        + _row("CSSF 22/805", "circular-cssf-22-805")
        + "</ul></body></html>"
    )
    details = {
        "/en/Document/circular-cssf-22-806/": DETAIL_22_806,
        "/en/Document/circular-cssf-22-805/": DETAIL_22_805,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.rstrip("/") == "/en/regulatory-framework":
            return httpx.Response(200, text=listing)
        if path in details:
            return httpx.Response(200, text=details[path])
        return httpx.Response(200, text="<html><body></body></html>")

    return httpx.MockTransport(handler)


def _sf(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_app_engine(tmp_path / "app.db")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()
    return sf


def _reg(s: Session, ref: str, published: date) -> int:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR, reference_number=ref, title=ref,
        issuing_authority="CSSF", publication_date=published,
        lifecycle_stage=LifecycleStage.IN_FORCE, is_ict=False, url="",
        source_of_truth="CSSF_WEB",
    )
    s.add(reg)
    s.flush()
    return reg.regulation_id


def _link(s: Session, from_id: int, to_id: int) -> None:
    s.add(RegulationLifecycleLink(
        from_regulation_id=from_id, to_regulation_id=to_id, relation="AMENDS",
    ))


def _links(sf: sessionmaker[Session]) -> set[tuple[str, str]]:
    with sf() as s:
        refs = dict(s.execute(select(Regulation.regulation_id, Regulation.reference_number)).all())
        return {
            (refs[link.from_regulation_id], refs[link.to_regulation_id])
            for link in s.scalars(select(RegulationLifecycleLink)).all()
        }


def _svc(sf: sessionmaker[Session]) -> CssfDiscoveryService:
    cfg = CssfDiscoveryConfig(
        request_delay_ms=0,
        publication_types=[
            PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR"),
        ],
        retire_min_scraped=0,
    )
    client = httpx.Client(transport=_transport(), base_url="https://www.cssf.lu")
    return CssfDiscoveryService(session_factory=sf, config=cfg, http_client=client)


def test_full_run_drops_links_the_crawl_does_not_support(tmp_path):
    sf = _sf(tmp_path)
    with sf() as s:
        r805 = _reg(s, "CSSF 22/805", date(2022, 4, 22))
        r806 = _reg(s, "CSSF 22/806", date(2022, 4, 22))
        r11 = _reg(s, "CSSF 11/512", date(2011, 5, 30))
        r18 = _reg(s, "CSSF 18/698", date(2018, 8, 23))
        r19 = _reg(s, "CSSF 19/001", date(2019, 1, 1))
        r17 = _reg(s, "CSSF 17/002", date(2017, 1, 1))
        _link(s, r806, r805)  # "related", both crawled -> unsupported
        _link(s, r11, r18)    # an older circular cannot amend a newer one
        _link(s, r19, r17)    # not crawled, plausible direction -> kept
        s.commit()

    _svc(sf).run(entity_types=["AIFM"], mode="full", triggered_by="TEST")

    links = _links(sf)
    assert ("CSSF 22/806", "CSSF 22/805") not in links
    assert ("CSSF 11/512", "CSSF 18/698") not in links
    assert ("CSSF 19/001", "CSSF 17/002") in links
    # Supported by 22/806's title "(as amended by Circular CSSF 25/883)".
    assert ("CSSF 25/883", "CSSF 22/806") in links


def test_incremental_run_keeps_existing_links(tmp_path):
    sf = _sf(tmp_path)
    with sf() as s:
        r805 = _reg(s, "CSSF 22/805", date(2022, 4, 22))
        r806 = _reg(s, "CSSF 22/806", date(2022, 4, 22))
        _link(s, r806, r805)
        s.commit()

    _svc(sf).run(entity_types=["AIFM"], mode="incremental", triggered_by="TEST")

    assert ("CSSF 22/806", "CSSF 22/805") in _links(sf)


def test_amended_targets_missing_from_catalog_get_no_stub(tmp_path):
    """22/806 amends 20/758, 04/155, IML 98/143 ... none of which are in
    this catalog. Stubbing them would add bare IN_FORCE rows for circulars
    that don't apply to the configured entities."""
    sf = _sf(tmp_path)
    _svc(sf).run(entity_types=["AIFM"], mode="full", triggered_by="TEST")
    with sf() as s:
        stubs = set(s.scalars(
            select(Regulation.reference_number).where(
                Regulation.source_of_truth == "CSSF_STUB"
            )
        ).all())
    assert stubs == {"CSSF 25/883"}
