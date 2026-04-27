"""Unit tests for SidebarBadgeService."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationType,
    Setting,
    UpdateEvent,
)
from regwatch.services.sidebar_badges import (
    SECTION_KEYS,
    SidebarBadgeService,
)


def _session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'badges.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_regulation(session, *, ref, lifecycle, is_ict, deadline=None, created_at=None):
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=ref,
        title=ref,
        issuing_authority="CSSF",
        lifecycle_stage=lifecycle,
        is_ict=is_ict,
        source_of_truth="SEED",
        url=f"https://example.com/{ref}",
        transposition_deadline=deadline,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(reg)
    session.flush()
    return reg


def _add_event(session, *, fetched_at, content_hash):
    ev = UpdateEvent(
        source="cssf_rss",
        source_url=f"https://example.com/ev/{content_hash}",
        title=content_hash,
        published_at=fetched_at,
        fetched_at=fetched_at,
        raw_payload={},
        content_hash=content_hash,
        is_ict=False,
        severity="INFORMATIONAL",
        review_status="NEW",
    )
    session.add(ev)
    session.flush()
    return ev


def test_section_keys_are_the_five_expected_sections():
    assert SECTION_KEYS == {
        "inbox": "last_visit_inbox",
        "catalog": "last_visit_catalog",
        "ict": "last_visit_ict",
        "drafts": "last_visit_drafts",
        "deadlines": "last_visit_deadlines",
    }


def test_missing_setting_keys_return_zero_counts(tmp_path):
    session = _session(tmp_path)
    _add_regulation(
        session, ref="A", lifecycle=LifecycleStage.IN_FORCE, is_ict=True
    )
    session.commit()

    counts = SidebarBadgeService(session).counts()
    assert counts.inbox == 0
    assert counts.catalog == 0
    assert counts.ict == 0
    assert counts.drafts == 0
    assert counts.deadlines == 0


def test_inbox_counts_events_after_last_visit(tmp_path):
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_inbox", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    _add_event(
        session, fetched_at=cutoff - timedelta(days=1), content_hash="old",
    )
    _add_event(
        session, fetched_at=cutoff + timedelta(days=1), content_hash="new1",
    )
    _add_event(
        session, fetched_at=cutoff + timedelta(days=2), content_hash="new2",
    )
    session.commit()

    assert SidebarBadgeService(session).counts().inbox == 2


def test_catalog_counts_regulations_after_last_visit(tmp_path):
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_catalog", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    _add_regulation(
        session, ref="OLD", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=False, created_at=cutoff - timedelta(days=1),
    )
    _add_regulation(
        session, ref="NEW", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=False, created_at=cutoff + timedelta(days=1),
    )
    session.commit()

    assert SidebarBadgeService(session).counts().catalog == 1


def test_ict_counts_only_is_ict_true(tmp_path):
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_ict", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    _add_regulation(
        session, ref="ICT", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=True, created_at=cutoff + timedelta(days=1),
    )
    _add_regulation(
        session, ref="NOT", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=False, created_at=cutoff + timedelta(days=1),
    )
    session.commit()

    assert SidebarBadgeService(session).counts().ict == 1


def test_drafts_counts_only_drafty_lifecycles(tmp_path):
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_drafts", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    for lc in (
        LifecycleStage.CONSULTATION,
        LifecycleStage.PROPOSAL,
        LifecycleStage.DRAFT_BILL,
        LifecycleStage.ADOPTED_NOT_IN_FORCE,
        LifecycleStage.IN_FORCE,  # excluded
        LifecycleStage.REPEALED,  # excluded
    ):
        _add_regulation(
            session, ref=lc.value, lifecycle=lc, is_ict=False,
            created_at=cutoff + timedelta(days=1),
        )
    session.commit()

    assert SidebarBadgeService(session).counts().drafts == 4


def test_deadlines_counts_regulations_with_any_deadline_set(tmp_path):
    from datetime import date
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_deadlines", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    _add_regulation(
        session, ref="HAS_DL", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=date(2027, 1, 1), created_at=cutoff + timedelta(days=1),
    )
    _add_regulation(
        session, ref="NO_DL", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=None, created_at=cutoff + timedelta(days=1),
    )
    session.commit()

    assert SidebarBadgeService(session).counts().deadlines == 1


def test_mark_visited_upserts_setting(tmp_path):
    session = _session(tmp_path)
    svc = SidebarBadgeService(session)

    before = datetime.now(UTC)
    svc.mark_visited("inbox")
    session.commit()
    after = datetime.now(UTC)

    row = session.get(Setting, "last_visit_inbox")
    assert row is not None
    stored = datetime.fromisoformat(row.value)
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert before <= stored <= after

    # Second call updates, does not duplicate.
    svc.mark_visited("inbox")
    session.commit()
    rows = session.query(Setting).filter(Setting.key == "last_visit_inbox").all()
    assert len(rows) == 1


def test_mark_visited_rejects_unknown_section(tmp_path):
    import pytest
    session = _session(tmp_path)
    svc = SidebarBadgeService(session)
    with pytest.raises(ValueError, match="unknown section"):
        svc.mark_visited("nope")
