from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.domain.types import (
    ExtractedDocument,
    MatchedDocument,
    MatchedReference,
    RawDocument,
)
from regwatch.pipeline.persist import persist_matched


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_reg(session: Session, reference: str) -> int:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=reference,
        title=reference,
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()
    return reg.regulation_id


def _matched(
    text: str, *, references: list[int], url: str = "https://example.com/a"
) -> MatchedDocument:
    now = datetime.now(timezone.utc)
    raw = RawDocument(
        source="cssf_rss",
        source_url=url,
        title="Sample",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )
    ext = ExtractedDocument(
        raw=raw,
        html_text=text,
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    return MatchedDocument(
        extracted=ext,
        references=[
            MatchedReference(regulation_id=rid, method="REGEX_ALIAS", confidence=1.0)
            for rid in references
        ],
        lifecycle_stage="IN_FORCE",
        is_ict=False,
        severity="MATERIAL",
    )


def test_persist_creates_event_and_links(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rid = _add_reg(session, "CSSF 18/698")
    session.commit()

    result = persist_matched(session, _matched("text v1", references=[rid]))
    session.commit()

    assert result.events_created == 1
    assert result.versions_created == 1

    ev = session.query(UpdateEvent).one()
    assert ev.source == "cssf_rss"
    links = session.query(UpdateEventRegulationLink).all()
    assert len(links) == 1
    versions = session.query(DocumentVersion).all()
    assert len(versions) == 1
    assert versions[0].version_number == 1
    assert versions[0].is_current is True


def test_persist_is_idempotent(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rid = _add_reg(session, "CSSF 18/698")
    session.commit()

    m = _matched("text v1", references=[rid])
    persist_matched(session, m)
    persist_matched(session, m)
    session.commit()

    assert session.query(UpdateEvent).count() == 1
    assert session.query(DocumentVersion).count() == 1


def test_persist_creates_new_version_on_content_change(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rid = _add_reg(session, "CSSF 18/698")
    session.commit()

    persist_matched(
        session, _matched("original", references=[rid], url="https://example.com/v1")
    )
    session.commit()
    persist_matched(
        session,
        _matched("revised text", references=[rid], url="https://example.com/v2"),
    )
    session.commit()

    versions = (
        session.query(DocumentVersion)
        .filter(DocumentVersion.regulation_id == rid)
        .order_by(DocumentVersion.version_number)
        .all()
    )
    assert len(versions) == 2
    assert versions[0].is_current is False
    assert versions[1].is_current is True
    assert versions[1].version_number == 2
    assert versions[1].change_summary is not None
    assert "-original" in versions[1].change_summary
    assert "+revised text" in versions[1].change_summary
