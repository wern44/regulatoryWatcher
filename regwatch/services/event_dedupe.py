"""Remove Inbox events that are copies of the same feed item.

Before the pipeline skipped items it already had (same source, URL and
publication date), every run downloaded them again; pages whose markup
changed cosmetically got a new content hash and were stored a second time.
This keeps the earliest copy of each item, carries over a review status
from a removed copy when the kept one is still NEW, and deletes the rest
with their regulation links. A republication (new publication date) is a
separate item and is kept.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from regwatch.db.models import UpdateEvent, UpdateEventRegulationLink


def remove_duplicate_events(session: Session, *, apply: bool) -> int:
    """Return how many copies exist (``apply=False``) or were removed.
    The caller commits."""
    items: dict[tuple[str, str, datetime], list[UpdateEvent]] = defaultdict(list)
    for event in session.scalars(select(UpdateEvent).order_by(UpdateEvent.event_id)):
        items[(event.source, event.source_url, event.published_at)].append(event)

    copies = [(events[0], events[1:]) for events in items.values() if len(events) > 1]
    removed = sum(len(rest) for _, rest in copies)
    if not apply:
        return removed

    for kept, rest in copies:
        if kept.review_status == "NEW":
            reviewed = [e for e in rest if e.review_status != "NEW"]
            if reviewed:
                kept.review_status = reviewed[-1].review_status
                kept.seen_at = reviewed[-1].seen_at
        doomed = [e.event_id for e in rest]
        session.query(UpdateEventRegulationLink).filter(
            UpdateEventRegulationLink.event_id.in_(doomed)
        ).delete(synchronize_session=False)
        session.query(UpdateEvent).filter(UpdateEvent.event_id.in_(doomed)).delete(
            synchronize_session=False
        )
    session.flush()
    return removed
