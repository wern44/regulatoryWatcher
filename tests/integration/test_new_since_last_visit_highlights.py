"""Highlight 'new since last visit' rows on Catalog/ICT/Drafts/Deadlines."""
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from regwatch.db.models import LifecycleStage, Regulation, RegulationType, Setting
from tests.integration.test_app_smoke import _client


def _seed_regulation(client, *, ref, lifecycle, is_ict, created_at, deadline=None):
    sf = client.app.state.session_factory
    with sf() as session:
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
            created_at=created_at,
        )
        session.add(reg)
        session.commit()
        return reg.regulation_id


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


def _row_block(html: str, reference: str) -> str:
    """Return the <tr>...</tr> block that contains the given reference number."""
    # rows are emitted as <tr ...>...{{ ref }}...</tr>; split is robust enough
    # for these template-driven tests.
    parts = html.split("<tr")
    for part in parts[1:]:
        block = "<tr" + part.split("</tr>", 1)[0] + "</tr>"
        if reference in block:
            return block
    raise AssertionError(f"reference {reference!r} not found in any <tr> block")


def test_catalog_highlights_new_rows_on_first_visit_after_cutoff(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client, ref="OLDREG", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=cutoff - timedelta(days=1),
    )
    _seed_regulation(
        client, ref="NEWREG", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=datetime.now(UTC),
    )

    resp = client.get("/catalog")
    assert resp.status_code == 200

    new_block = _row_block(resp.text, "NEWREG")
    old_block = _row_block(resp.text, "OLDREG")

    assert "bg-amber-50" in new_block
    assert ">NEW<" in new_block
    assert "bg-amber-50" not in old_block
    assert ">NEW<" not in old_block


def test_catalog_no_highlight_on_second_visit(tmp_path: Path, monkeypatch) -> None:
    """After the first visit advances the cutoff, the row is no longer 'new'."""
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client, ref="NEWREG", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=datetime.now(UTC),
    )

    # First visit: row highlighted, cutoff advances to now.
    client.get("/catalog")
    # Second visit: cutoff is now in the future relative to NEWREG.created_at.
    resp = client.get("/catalog")
    assert resp.status_code == 200
    block = _row_block(resp.text, "NEWREG")
    assert ">NEW<" not in block


def test_catalog_no_highlight_when_no_prior_visit(
    tmp_path: Path, monkeypatch
) -> None:
    """First-ever visit (no last_visit_catalog) should not highlight anything,
    matching the badge which is also 0 in this case."""
    client = _client(tmp_path, monkeypatch)
    _seed_regulation(
        client, ref="ANYREG", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=datetime.now(UTC),
    )

    resp = client.get("/catalog")
    assert resp.status_code == 200
    block = _row_block(resp.text, "ANYREG")
    assert ">NEW<" not in block


def test_drafts_highlights_new_drafty_rows(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_drafts", ts=cutoff)
    _seed_regulation(
        client, ref="OLDDRAFT", lifecycle=LifecycleStage.PROPOSAL, is_ict=False,
        created_at=cutoff - timedelta(days=1),
    )
    _seed_regulation(
        client, ref="NEWDRAFT", lifecycle=LifecycleStage.CONSULTATION, is_ict=False,
        created_at=datetime.now(UTC),
    )

    resp = client.get("/drafts")
    assert resp.status_code == 200

    new_block = _row_block(resp.text, "NEWDRAFT")
    old_block = _row_block(resp.text, "OLDDRAFT")

    assert "bg-amber-50" in new_block
    assert ">NEW<" in new_block
    assert "bg-amber-50" not in old_block
    assert ">NEW<" not in old_block
