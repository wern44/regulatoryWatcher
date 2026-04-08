from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, UpdateEvent
from regwatch.services.inbox import InboxService


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_event(
    session: Session,
    *,
    severity: str,
    review_status: str = "NEW",
    title: str = "Sample",
    source: str = "cssf_rss",
    content_hash: str | None = None,
    published_at: datetime | None = None,
) -> UpdateEvent:
    ev = UpdateEvent(
        source=source,
        source_url=f"https://example.com/{content_hash or title}",
        title=title,
        published_at=published_at or datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        raw_payload={},
        content_hash=(content_hash or title).ljust(64, "x"),
        is_ict=False,
        severity=severity,
        review_status=review_status,
    )
    session.add(ev)
    session.flush()
    return ev


def test_list_new_ignores_seen_and_archived(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_event(
        session, severity="MATERIAL", review_status="NEW", content_hash="a"
    )
    _add_event(
        session, severity="MATERIAL", review_status="SEEN", content_hash="b"
    )
    _add_event(
        session, severity="MATERIAL", review_status="ARCHIVED", content_hash="c"
    )
    session.commit()

    svc = InboxService(session)
    new_events = svc.list_new()
    assert len(new_events) == 1


def test_count_new_only_counts_new(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_event(
        session, severity="CRITICAL", review_status="NEW", content_hash="a"
    )
    _add_event(
        session, severity="MATERIAL", review_status="NEW", content_hash="b"
    )
    _add_event(
        session, severity="MATERIAL", review_status="SEEN", content_hash="c"
    )
    session.commit()

    svc = InboxService(session)
    assert svc.count_new() == 2


def test_list_new_orders_by_severity_then_published_at_desc(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    now = datetime.now(timezone.utc)
    _add_event(
        session,
        severity="MATERIAL",
        title="m_older",
        content_hash="1",
        published_at=now - timedelta(days=2),
    )
    _add_event(
        session,
        severity="CRITICAL",
        title="c_newest",
        content_hash="2",
        published_at=now,
    )
    _add_event(
        session,
        severity="INFORMATIONAL",
        title="i_any",
        content_hash="3",
        published_at=now - timedelta(hours=1),
    )
    _add_event(
        session,
        severity="MATERIAL",
        title="m_newer",
        content_hash="4",
        published_at=now - timedelta(days=1),
    )
    session.commit()

    svc = InboxService(session)
    ordered = svc.list_new()
    assert [e.title for e in ordered] == [
        "c_newest",
        "m_newer",
        "m_older",
        "i_any",
    ]


def test_list_by_severity(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_event(
        session,
        severity="CRITICAL",
        review_status="NEW",
        content_hash="a",
        title="crit",
    )
    _add_event(
        session,
        severity="MATERIAL",
        review_status="NEW",
        content_hash="b",
        title="mat",
    )
    session.commit()

    svc = InboxService(session)
    crit = svc.list_by_severity("CRITICAL")
    assert len(crit) == 1
    assert crit[0].title == "crit"


def test_mark_seen_sets_seen_at(tmp_path: Path) -> None:
    session = _session(tmp_path)
    ev = _add_event(session, severity="MATERIAL", content_hash="a")
    session.commit()

    svc = InboxService(session)
    svc.mark_seen(ev.event_id)
    session.commit()

    refreshed = session.get(UpdateEvent, ev.event_id)
    assert refreshed is not None
    assert refreshed.review_status == "SEEN"
    assert refreshed.seen_at is not None


def test_archive_transitions_to_archived(tmp_path: Path) -> None:
    session = _session(tmp_path)
    ev = _add_event(session, severity="MATERIAL", content_hash="a")
    session.commit()

    svc = InboxService(session)
    svc.archive(ev.event_id)
    session.commit()

    refreshed = session.get(UpdateEvent, ev.event_id)
    assert refreshed is not None
    assert refreshed.review_status == "ARCHIVED"
