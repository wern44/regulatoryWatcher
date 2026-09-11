"""Removing Inbox events stored twice before the pipeline skipped known items."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationType,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.services.event_dedupe import remove_duplicate_events

PUBLISHED = datetime(2026, 4, 3, 4, 0, tzinfo=UTC)


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "app.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _event(s: Session, n: int, url: str, *, published: datetime = PUBLISHED,
           status: str = "NEW", reg_id: int | None = None) -> int:
    now = datetime.now(UTC)
    e = UpdateEvent(
        source="cssf_rss", source_url=url, title=url, published_at=published,
        fetched_at=now, raw_payload={}, content_hash=f"{n:064d}", is_ict=False,
        severity="INFORMATIONAL", review_status=status,
        seen_at=now if status != "NEW" else None,
    )
    if reg_id is not None:
        e.regulation_links.append(UpdateEventRegulationLink(
            regulation_id=reg_id, match_method="REGEX_ALIAS", confidence=1.0,
        ))
    s.add(e)
    s.flush()
    return e.event_id


def _seed(s: Session) -> dict[str, int]:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 18/698", title="t",
        issuing_authority="CSSF", lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False, url="", source_of_truth="SEED",
    )
    s.add(reg)
    s.flush()
    ids = {
        "a_first": _event(s, 1, "https://x/a", status="SEEN", reg_id=reg.regulation_id),
        "a_copy": _event(s, 2, "https://x/a", reg_id=reg.regulation_id),
        "b_first": _event(s, 3, "https://x/b"),
        "b_copy": _event(s, 4, "https://x/b", status="ARCHIVED"),
        "c_first": _event(s, 5, "https://x/c"),
        "c_republished": _event(s, 6, "https://x/c", published=datetime(2026, 9, 1, tzinfo=UTC)),
    }
    s.commit()
    return ids


def test_dry_run_counts_only_same_item_copies(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed(s)

    assert remove_duplicate_events(s, apply=False) == 2
    assert s.query(UpdateEvent).count() == 6


def test_apply_keeps_the_earliest_copy_and_its_review(tmp_path: Path) -> None:
    s = _session(tmp_path)
    ids = _seed(s)

    assert remove_duplicate_events(s, apply=True) == 2
    s.commit()

    remaining = {e.event_id: e.review_status for e in s.query(UpdateEvent).all()}
    assert remaining == {
        ids["a_first"]: "SEEN",
        ids["b_first"]: "ARCHIVED",  # the removed copy had been reviewed
        ids["c_first"]: "NEW",
        ids["c_republished"]: "NEW",  # a republication, not a duplicate
    }
    assert [link.event_id for link in s.query(UpdateEventRegulationLink).all()] == [
        ids["a_first"]
    ]
    assert remove_duplicate_events(s, apply=True) == 0
