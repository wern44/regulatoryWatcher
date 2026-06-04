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


def _add_event(session, *, fetched_at, content_hash, review_status="NEW"):
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
        review_status=review_status,
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


def test_inbox_counts_events_with_review_status_NEW(tmp_path):
    """Inbox badge tracks the same items the inbox page shows: NEW events."""
    session = _session(tmp_path)
    now = datetime.now(UTC)
    _add_event(
        session, fetched_at=now, content_hash="new1", review_status="NEW",
    )
    _add_event(
        session, fetched_at=now, content_hash="new2", review_status="NEW",
    )
    session.commit()

    assert SidebarBadgeService(session).counts().inbox == 2


def test_inbox_does_not_count_seen_or_archived(tmp_path):
    session = _session(tmp_path)
    now = datetime.now(UTC)
    _add_event(
        session, fetched_at=now, content_hash="new1", review_status="NEW",
    )
    _add_event(
        session, fetched_at=now, content_hash="seen", review_status="SEEN",
    )
    _add_event(
        session, fetched_at=now, content_hash="arch", review_status="ARCHIVED",
    )
    session.commit()

    assert SidebarBadgeService(session).counts().inbox == 1


def test_inbox_count_does_not_depend_on_last_visit_setting(tmp_path):
    """Per the new semantic, mark_visited('inbox') does NOT clear the inbox
    badge. Only triaging events (mark-seen, archive) reduces the count."""
    session = _session(tmp_path)
    now = datetime.now(UTC)
    _add_event(session, fetched_at=now, content_hash="n1", review_status="NEW")
    session.commit()

    # Even if last_visit_inbox is set to "now", the NEW event still counts.
    SidebarBadgeService(session).mark_visited("inbox")
    session.commit()
    assert SidebarBadgeService(session).counts().inbox == 1


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


def test_ict_counts_only_is_ict_true_and_in_force(tmp_path):
    """ICT badge mirrors the /ict page: is_ict=True AND lifecycle=IN_FORCE."""
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_ict", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    # Visible: ICT + IN_FORCE.
    _add_regulation(
        session, ref="ICT", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=True, created_at=cutoff + timedelta(days=1),
    )
    # Hidden: not ICT.
    _add_regulation(
        session, ref="NOT", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=False, created_at=cutoff + timedelta(days=1),
    )
    # Hidden: ICT but lifecycle is AMENDED.
    _add_regulation(
        session, ref="AMENDED", lifecycle=LifecycleStage.AMENDED,
        is_ict=True, created_at=cutoff + timedelta(days=1),
    )
    # Hidden: ICT but lifecycle is REPEALED.
    _add_regulation(
        session, ref="REPEALED", lifecycle=LifecycleStage.REPEALED,
        is_ict=True, created_at=cutoff + timedelta(days=1),
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


def test_deadlines_counts_only_in_window_and_not_done(tmp_path):
    """Badge filter must mirror the /deadlines page: in window, not done."""
    from datetime import date, timedelta as td
    session = _session(tmp_path)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    session.add(Setting(
        key="last_visit_deadlines", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    today = date.today()
    in_window = today + td(days=180)
    out_of_window = today + td(days=900)  # > 730 days

    # Visible: deadline within 730 days, not done.
    _add_regulation(
        session, ref="VISIBLE", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=in_window, created_at=datetime.now(UTC),
    )
    # Hidden: deadline beyond 730 days.
    _add_regulation(
        session, ref="FAR_FUTURE", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=out_of_window, created_at=datetime.now(UTC),
    )
    # Hidden: no deadline at all.
    _add_regulation(
        session, ref="NO_DL", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=None, created_at=datetime.now(UTC),
    )
    # Hidden: deadline in window but already marked done.
    done_reg = _add_regulation(
        session, ref="DONE", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=in_window, created_at=datetime.now(UTC),
    )
    done_reg.transposition_done = True
    session.commit()

    assert SidebarBadgeService(session).counts().deadlines == 1


def test_deadlines_counts_application_date_too(tmp_path):
    """application_date is the second deadline kind; it should also count."""
    from datetime import date, timedelta as td
    session = _session(tmp_path)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    session.add(Setting(
        key="last_visit_deadlines", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    today = date.today()
    reg = _add_regulation(
        session, ref="APP_DL", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=None, created_at=datetime.now(UTC),
    )
    reg.application_date = today + td(days=90)
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


def test_mark_visited_returns_previous_timestamp(tmp_path):
    """mark_visited() returns the prior last_visit_<section> value (or None)
    and overwrites the stored value with `now`. The route uses the returned
    value as the cutoff for highlighting `new` rows on this same render."""
    session = _session(tmp_path)
    svc = SidebarBadgeService(session)

    # First call: no prior value -> returns None, stores now.
    previous = svc.mark_visited("catalog")
    session.commit()
    assert previous is None
    stored1 = datetime.fromisoformat(
        session.get(Setting, "last_visit_catalog").value
    )
    if stored1.tzinfo is None:
        stored1 = stored1.replace(tzinfo=UTC)

    # Second call: returns the previously-stored timestamp, advances the value.
    previous2 = svc.mark_visited("catalog")
    session.commit()
    assert previous2 is not None
    assert previous2 == stored1
    stored2 = datetime.fromisoformat(
        session.get(Setting, "last_visit_catalog").value
    )
    if stored2.tzinfo is None:
        stored2 = stored2.replace(tzinfo=UTC)
    assert stored2 >= stored1
