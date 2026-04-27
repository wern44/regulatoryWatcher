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


def test_visiting_inbox_clears_only_inbox_badge(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_inbox", ts=cutoff)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client, ref="REG1", is_ict=False,
        lifecycle=LifecycleStage.IN_FORCE, created_at=datetime.now(UTC),
    )
    # Insert one update_event manually for the inbox count.
    sf = client.app.state.session_factory
    from regwatch.db.models import UpdateEvent
    with sf() as session:
        session.add(UpdateEvent(
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
        ))
        session.commit()

    # Before visiting inbox: both Inbox and Catalog have a pill.
    before = client.get("/")
    inbox_before = before.text.split('href="/inbox"', 1)[1].split("</a>", 1)[0]
    cat_before = before.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" in inbox_before
    assert "bg-amber-500" in cat_before

    # Visit /inbox.
    client.get("/inbox")

    # After: Inbox cleared, Catalog still pillared.
    after = client.get("/")
    inbox_after = after.text.split('href="/inbox"', 1)[1].split("</a>", 1)[0]
    cat_after = after.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" not in inbox_after
    assert "bg-amber-500" in cat_after
