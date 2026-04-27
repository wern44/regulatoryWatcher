"""End-to-end: badge appears on sidebar, clears after the section is visited."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

from regwatch.db.models import LifecycleStage, Regulation, RegulationType, Setting
from tests.integration.test_app_smoke import _client


def _seed_regulation(client, *, ref, is_ict, lifecycle, created_at):
    sf = client.app.state.session_factory
    with sf() as session:
        session.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number=ref,
            title=ref,
            issuing_authority="CSSF",
            lifecycle_stage=lifecycle,
            is_ict=is_ict,
            source_of_truth="SEED",
            url=f"https://example.com/{ref}",
            created_at=created_at,
        ))
        session.commit()


def _set_last_visit(client, *, key, ts):
    sf = client.app.state.session_factory
    with sf() as session:
        existing = session.get(Setting, key)
        if existing is None:
            session.add(Setting(key=key, value=ts.isoformat(), updated_at=ts))
        else:
            existing.value = ts.isoformat()
            existing.updated_at = ts
        session.commit()


def test_catalog_badge_shows_then_clears(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client,
        ref="NEWREG",
        is_ict=False,
        lifecycle=LifecycleStage.IN_FORCE,
        created_at=datetime.now(UTC),
    )

    # Render Dashboard — sidebar should show "1" near the Catalog link.
    resp1 = client.get("/")
    assert resp1.status_code == 200
    assert 'href="/catalog"' in resp1.text
    catalog_block = resp1.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" in catalog_block
    assert ">1<" in catalog_block or ">\n      1\n    <" in catalog_block

    # Visit /catalog — clears the badge.
    resp2 = client.get("/catalog")
    assert resp2.status_code == 200

    # Render Dashboard again — sidebar's catalog row should NOT carry a pill.
    resp3 = client.get("/")
    assert resp3.status_code == 200
    catalog_block3 = resp3.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" not in catalog_block3


def test_visiting_clearing_route_clears_badge_in_same_response(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: clicking /catalog should clear the catalog pill within the
    same response, not only on the next render. The route's mark_visited()
    commits before render_page() opens its read session, so the response's
    own sidebar must already reflect the cleared state.
    """
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client,
        ref="NEWREG2",
        is_ict=False,
        lifecycle=LifecycleStage.IN_FORCE,
        created_at=datetime.now(UTC),
    )

    # Confirm dashboard shows the badge first.
    pre = client.get("/")
    pre_block = pre.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" in pre_block

    # Visit /catalog. The sidebar in THIS response should already be cleared.
    visit = client.get("/catalog")
    assert visit.status_code == 200
    visit_block = visit.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" not in visit_block, (
        "Catalog badge should be cleared in the same response that the user "
        "navigated to /catalog (not just on the next render)."
    )


def test_ict_badge_only_counts_is_ict_true(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_ict", ts=cutoff)
    _seed_regulation(
        client, ref="ICT1", is_ict=True,
        lifecycle=LifecycleStage.IN_FORCE, created_at=datetime.now(UTC),
    )
    _seed_regulation(
        client, ref="NOT1", is_ict=False,
        lifecycle=LifecycleStage.IN_FORCE, created_at=datetime.now(UTC),
    )

    resp = client.get("/")
    assert resp.status_code == 200
    ict_block = resp.text.split('href="/ict"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" in ict_block
    assert ">1<" in ict_block or ">\n      1\n    <" in ict_block


def test_dashboard_link_never_carries_a_badge(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    # Even with everything fresh, the Dashboard link itself has no pill.
    resp = client.get("/")
    assert resp.status_code == 200
    # Use the FIRST href="/" occurrence (the Dashboard nav link).
    dashboard_block = resp.text.split('href="/"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" not in dashboard_block


def test_inbox_badge_tracks_new_events_until_triaged(
    tmp_path: Path, monkeypatch
) -> None:
    """Inbox badge counts events with review_status='NEW' — visiting /inbox
    does NOT clear it. Marking events SEEN or ARCHIVED is what reduces it."""
    client = _client(tmp_path, monkeypatch)
    sf = client.app.state.session_factory
    from regwatch.db.models import UpdateEvent
    with sf() as session:
        ev = UpdateEvent(
            source="cssf_rss",
            source_url="https://example.com/ev",
            title="ev",
            published_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
            raw_payload={},
            content_hash="hh" + "0" * 60,
            is_ict=False,
            severity="INFORMATIONAL",
            review_status="NEW",
        )
        session.add(ev)
        session.commit()
        event_id = ev.event_id

    # Inbox pill shows because the event is review_status=NEW.
    before = client.get("/")
    inbox_before = before.text.split('href="/inbox"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" in inbox_before

    # Visiting /inbox does NOT clear the pill — only triage does.
    client.get("/inbox")
    after_visit = client.get("/")
    inbox_after_visit = after_visit.text.split(
        'href="/inbox"', 1
    )[1].split("</a>", 1)[0]
    assert "bg-amber-500" in inbox_after_visit

    # Triaging the event (mark-seen) clears the pill.
    client.post(f"/inbox/{event_id}/mark-seen")
    after_triage = client.get("/")
    inbox_after_triage = after_triage.text.split(
        'href="/inbox"', 1
    )[1].split("</a>", 1)[0]
    assert "bg-amber-500" not in inbox_after_triage
