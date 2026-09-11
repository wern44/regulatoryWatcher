"""Integration tests for amendment grouping on the catalog and detail pages."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationLifecycleLink,
    RegulationType,
)
from tests.integration.test_app_smoke import _client  # noqa: E402

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _make_reg(
    session: Session,
    ref: str,
    lifecycle: LifecycleStage = LifecycleStage.IN_FORCE,
    *,
    is_ict: bool = False,
    published: date | None = None,
) -> Regulation:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=ref,
        title=f"Title of {ref}",
        issuing_authority="CSSF",
        publication_date=published,
        lifecycle_stage=lifecycle,
        is_ict=is_ict,
        needs_review=False,
        url="https://example.com",
        source_of_truth="SEED",
    )
    session.add(reg)
    session.flush()
    return reg


def _amends(session: Session, child: Regulation, parent: Regulation) -> None:
    session.add(RegulationLifecycleLink(
        from_regulation_id=child.regulation_id,
        to_regulation_id=parent.regulation_id,
        relation="AMENDS",
    ))


def _seed_db(db_file: Path) -> None:
    """Ensure tables exist in the given DB file."""
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Test 1 — catalog hides amendments by default
# ---------------------------------------------------------------------------

def test_catalog_hides_amendments_by_default(tmp_path: Path, monkeypatch) -> None:
    """A (top-level) visible; B (amends A, both IN_FORCE) hidden by default."""
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/001")
        b = _make_reg(session, "CSSF 21/002")
        _amends(session, b, a)
        session.commit()
        a_id = a.regulation_id

    resp = client.get("/catalog")
    assert resp.status_code == 200
    body = resp.text
    assert "CSSF 20/001" in body
    assert "CSSF 21/002" not in body
    # Badge for the one amendment
    assert "+1 amendments" in body
    assert f"/regulations/{a_id}#amendments" in body


# ---------------------------------------------------------------------------
# Test 2 — show_amendments toggle reveals both rows
# ---------------------------------------------------------------------------

def test_catalog_show_amendments_toggle(tmp_path: Path, monkeypatch) -> None:
    """?show_amendments=true makes B visible as a top-level row."""
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/001")
        b = _make_reg(session, "CSSF 21/002")
        _amends(session, b, a)
        session.commit()

    resp = client.get("/catalog?show_amendments=true")
    assert resp.status_code == 200
    body = resp.text
    assert "CSSF 20/001" in body
    assert "CSSF 21/002" in body


# ---------------------------------------------------------------------------
# Test 3 — orphan amendment of REPEALED parent is top-level
# ---------------------------------------------------------------------------

def test_catalog_shows_orphan_amendment_as_top_level(tmp_path: Path, monkeypatch) -> None:
    """A REPEALED + B amends A → B is top-level (parent is not IN_FORCE).

    Default lifecycle filter shows only IN_FORCE, so A is hidden on that
    basis. B appears as top-level because the REPEALED parent doesn't count.
    """
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 10/001", lifecycle=LifecycleStage.REPEALED)
        b = _make_reg(session, "CSSF 11/002", lifecycle=LifecycleStage.IN_FORCE)
        _amends(session, b, a)
        session.commit()

    resp = client.get("/catalog")
    assert resp.status_code == 200
    body = resp.text
    # A is REPEALED — filtered out by the default lifecycle=IN_FORCE filter
    assert "CSSF 10/001" not in body
    # B is IN_FORCE AND its only AMENDS target is REPEALED, so it's top-level
    assert "CSSF 11/002" in body


# ---------------------------------------------------------------------------
# Test 4 — parent detail lists amendments
# ---------------------------------------------------------------------------

def test_parent_detail_lists_amendments(tmp_path: Path, monkeypatch) -> None:
    """GET /regulations/<A id> has an 'Amendments (1)' section listing B."""
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/001")
        b = _make_reg(session, "CSSF 21/002")
        _amends(session, b, a)
        session.commit()
        a_id = a.regulation_id

    resp = client.get(f"/regulations/{a_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "Amendments (1)" in body
    assert "CSSF 21/002" in body


# ---------------------------------------------------------------------------
# Test 5 — amendment detail shows parent banner (non-repealed parent)
# ---------------------------------------------------------------------------

def test_amendment_detail_shows_parent_banner(tmp_path: Path, monkeypatch) -> None:
    """GET /regulations/<B id> shows 'This circular amends' banner for A."""
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/001")
        b = _make_reg(session, "CSSF 21/002")
        _amends(session, b, a)
        session.commit()
        b_id = b.regulation_id
        a_id = a.regulation_id

    resp = client.get(f"/regulations/{b_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "This circular amends" in body
    assert "CSSF 20/001" in body
    assert f"/regulations/{a_id}" in body


# ---------------------------------------------------------------------------
# Test 6 — amendment of REPEALED parent shows REPEALED banner variant
# ---------------------------------------------------------------------------

def test_amendment_of_repealed_parent_shows_banner_variant(
    tmp_path: Path, monkeypatch
) -> None:
    """GET /regulations/<B id> where A is REPEALED → banner says REPEALED."""
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 10/001", lifecycle=LifecycleStage.REPEALED)
        b = _make_reg(session, "CSSF 11/002", lifecycle=LifecycleStage.IN_FORCE)
        _amends(session, b, a)
        session.commit()
        b_id = b.regulation_id

    resp = client.get(f"/regulations/{b_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "REPEALED" in body
    assert "CSSF 10/001" in body


# ---------------------------------------------------------------------------
# Test 7 — chained amendments flatten to ancestor
# ---------------------------------------------------------------------------

def test_chained_amendments_flatten_to_ancestor(tmp_path: Path, monkeypatch) -> None:
    """A (IN_FORCE) ← B amends A ← C amends B (both IN_FORCE).

    Catalog default view → only A visible with '+2 amendments'.
    A's detail page lists both B and C in the Amendments section.
    """
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/001")
        b = _make_reg(session, "CSSF 21/002")
        c = _make_reg(session, "CSSF 22/003")
        _amends(session, b, a)
        _amends(session, c, b)
        session.commit()
        a_id = a.regulation_id

    # Catalog: only A visible, with +2 badge
    resp = client.get("/catalog")
    assert resp.status_code == 200
    body = resp.text
    assert "CSSF 20/001" in body
    assert "CSSF 21/002" not in body
    assert "CSSF 22/003" not in body
    assert "+2 amendments" in body

    # A's detail page lists both B and C
    resp2 = client.get(f"/regulations/{a_id}")
    assert resp2.status_code == 200
    body2 = resp2.text
    assert "Amendments (2)" in body2
    assert "CSSF 21/002" in body2
    assert "CSSF 22/003" in body2


def test_catalog_search_finds_amendments(tmp_path: Path, monkeypatch) -> None:
    """Searching for an amending circular's number must find it even though
    the default view rolls it up under the circular it amends."""
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 22/811")
        b = _make_reg(session, "CSSF 25/900")
        _amends(session, b, a)
        session.commit()

    resp = client.get("/catalog?search=25/900")
    assert resp.status_code == 200
    assert "CSSF 25/900" in resp.text


def test_catalog_shows_newest_amendment_date(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/750", published=date(2020, 8, 31))
        b = _make_reg(session, "CSSF 22/806", published=date(2022, 4, 22))
        _amends(session, b, a)
        _make_reg(session, "CSSF 18/698", published=date(2018, 8, 23))
        session.commit()

    body = client.get("/catalog").text
    assert "Last change" in body
    assert "2022-04-22" in body
    assert "via CSSF 22/806" in body
    # No amendments: the regulation's own publication date.
    assert "2018-08-23" in body


# ---------------------------------------------------------------------------
# ICT page — same roll-up as the catalog
# ---------------------------------------------------------------------------

def test_ict_rolls_amendments_up_under_their_circular(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/750", is_ict=True, published=date(2020, 8, 31))
        b = _make_reg(session, "CSSF 22/806", is_ict=True, published=date(2022, 4, 22))
        c = _make_reg(session, "CSSF 24/900", is_ict=False, published=date(2024, 3, 1))
        _amends(session, b, a)
        _amends(session, c, a)
        session.commit()
        a_id = a.regulation_id

    body = client.get("/ict").text
    assert "CSSF 20/750" in body
    assert "CSSF 22/806" not in body
    assert "+2 amendments" in body
    assert f"/regulations/{a_id}#amendments" in body
    assert "2024-03-01" in body
    assert "via CSSF 24/900" in body


def test_ict_show_amendments_toggle(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/750", is_ict=True)
        b = _make_reg(session, "CSSF 22/806", is_ict=True)
        _amends(session, b, a)
        session.commit()

    body = client.get("/ict?show_amendments=true").text
    assert "CSSF 20/750" in body
    assert "CSSF 22/806" in body


def test_ict_keeps_amendment_of_non_ict_circular(tmp_path: Path, monkeypatch) -> None:
    """The circular it amends is not on the ICT page, so the amendment
    stays as its own row instead of disappearing."""
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 18/698", is_ict=False)
        b = _make_reg(session, "CSSF 22/811", is_ict=True)
        _amends(session, b, a)
        session.commit()

    body = client.get("/ict").text
    assert "CSSF 22/811" in body
    assert "CSSF 18/698" not in body


# ---------------------------------------------------------------------------
# Recent changes (last 3 months) and sortable tables
# ---------------------------------------------------------------------------

def test_catalog_and_ict_highlight_recent_changes(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_db(tmp_path / "app.db")
    recent = date.today() - timedelta(days=10)

    with client.app.state.session_factory() as session:
        a = _make_reg(session, "CSSF 20/750", is_ict=True, published=date(2020, 8, 31))
        b = _make_reg(session, "CSSF 26/915", is_ict=True, published=recent)
        _amends(session, b, a)
        _make_reg(session, "CSSF 18/698", is_ict=True, published=date(2018, 8, 23))
        session.commit()

    for url in ("/catalog", "/ict"):
        body = client.get(url).text
        assert 'data-sortable=' in body
        assert 'data-sort="date"' in body
        assert f'data-sort-value="{recent.isoformat()}"' in body
        # One highlighted row: 20/750, changed via its recent amendment.
        assert body.count(">Recent</span>") == 1
        assert "via CSSF 26/915" in body

