from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, UpdateEvent
from tests.integration.test_app_smoke import _client


def _seed_event(
    db_file: Path,
    *,
    title: str = "Sample event",
    content_hash: str = "a" * 64,
    review_status: str = "NEW",
) -> int:
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ev = UpdateEvent(
            source="cssf_rss",
            source_url="https://example.com/e",
            title=title,
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
            raw_payload={},
            content_hash=content_hash,
            is_ict=False,
            severity="MATERIAL",
            review_status=review_status,
        )
        session.add(ev)
        session.commit()
        return ev.event_id


def test_inbox_list_returns_events(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_event(tmp_path / "app.db", title="Amendment to CSSF 18/698")

    response = client.get("/inbox")
    assert response.status_code == 200
    assert "Amendment to CSSF 18/698" in response.text


def test_mark_seen_removes_from_new_list(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    eid = _seed_event(tmp_path / "app.db")

    r = client.post(f"/inbox/{eid}/mark-seen")
    assert r.status_code == 200

    # Check db state
    engine = create_app_engine(tmp_path / "app.db")
    with Session(engine) as session:
        ev = session.get(UpdateEvent, eid)
        assert ev is not None
        assert ev.review_status == "SEEN"

    listing = client.get("/inbox")
    # The SEEN event should no longer appear in the NEW list.
    assert "Sample event" not in listing.text


def test_archive_removes_from_new_list(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    eid = _seed_event(tmp_path / "app.db")

    r = client.post(f"/inbox/{eid}/archive")
    assert r.status_code == 200

    engine = create_app_engine(tmp_path / "app.db")
    with Session(engine) as session:
        ev = session.get(UpdateEvent, eid)
        assert ev is not None
        assert ev.review_status == "ARCHIVED"
