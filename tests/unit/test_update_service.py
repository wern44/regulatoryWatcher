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
from regwatch.services.updates import UpdateService


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_reg(session: Session, reference: str) -> Regulation:
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
    return reg


def _add_version(
    session: Session,
    reg: Regulation,
    *,
    number: int,
    text: str,
    is_current: bool,
) -> DocumentVersion:
    v = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=number,
        is_current=is_current,
        fetched_at=datetime.now(timezone.utc),
        source_url=f"https://example.com/v{number}",
        content_hash=str(number).ljust(64, str(number)),
        html_text=text,
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(v)
    session.flush()
    return v


def test_get_event_returns_linked_regulations(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_reg(session, "CSSF 18/698")
    _add_version(session, reg, number=1, text="body v1", is_current=True)

    ev = UpdateEvent(
        source="cssf_rss",
        source_url="https://example.com",
        title="Amendment event",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        raw_payload={},
        content_hash="h" * 64,
        is_ict=False,
        severity="MATERIAL",
        review_status="NEW",
    )
    ev.regulation_links.append(
        UpdateEventRegulationLink(
            regulation_id=reg.regulation_id,
            match_method="REGEX_ALIAS",
            confidence=1.0,
            matched_snippet="CSSF 18/698",
        )
    )
    session.add(ev)
    session.commit()

    svc = UpdateService(session)
    detail = svc.get_event(ev.event_id)
    assert detail is not None
    assert detail.title == "Amendment event"
    assert len(detail.regulations) == 1
    assert detail.regulations[0].reference_number == "CSSF 18/698"


def test_list_versions_ordered(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_reg(session, "CSSF 18/698")
    _add_version(session, reg, number=1, text="v1", is_current=False)
    _add_version(session, reg, number=2, text="v2", is_current=False)
    _add_version(session, reg, number=3, text="v3", is_current=True)
    session.commit()

    svc = UpdateService(session)
    versions = svc.list_versions(reg.regulation_id)
    assert [v.version_number for v in versions] == [1, 2, 3]


def test_compare_versions_returns_diff(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_reg(session, "CSSF 18/698")
    _add_version(
        session, reg, number=1, text="original text\n", is_current=False
    )
    _add_version(session, reg, number=2, text="middle text\n", is_current=False)
    _add_version(session, reg, number=3, text="revised text\n", is_current=True)
    session.commit()

    svc = UpdateService(session)
    diff = svc.compare_versions(reg.regulation_id, 1, 3)
    assert diff is not None
    assert "-original text" in diff.diff_text
    assert "+revised text" in diff.diff_text
