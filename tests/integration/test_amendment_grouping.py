"""Integration tests for amendment grouping on the catalog and detail pages."""
from __future__ import annotations

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
) -> Regulation:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=ref,
        title=f"Title of {ref}",
        issuing_authority="CSSF",
        lifecycle_stage=lifecycle,
        is_ict=False,
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
