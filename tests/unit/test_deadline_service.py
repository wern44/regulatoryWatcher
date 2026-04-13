from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.services.deadlines import DeadlineService


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_reg(
    session: Session,
    ref: str,
    *,
    transposition_deadline: date | None = None,
    application_date: date | None = None,
    transposition_done: bool = False,
    application_done: bool = False,
) -> None:
    reg = Regulation(
        type=RegulationType.EU_DIRECTIVE,
        reference_number=ref,
        title=ref,
        issuing_authority="EU",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
        transposition_deadline=transposition_deadline,
        application_date=application_date,
        transposition_done=transposition_done,
        application_done=application_done,
    )
    session.add(reg)


def test_severity_band() -> None:
    svc_cls = DeadlineService
    assert svc_cls.severity_band(-1) == "OVERDUE"
    assert svc_cls.severity_band(10) == "RED"
    assert svc_cls.severity_band(30) == "RED"
    assert svc_cls.severity_band(60) == "AMBER"
    assert svc_cls.severity_band(200) == "BLUE"
    assert svc_cls.severity_band(800) == "GREY"


def test_upcoming_returns_sorted_by_days_until(tmp_path: Path) -> None:
    session = _session(tmp_path)
    today = date.today()
    _add_reg(
        session,
        "Near",
        transposition_deadline=today + timedelta(days=10),
    )
    _add_reg(
        session,
        "Far",
        application_date=today + timedelta(days=365),
    )
    _add_reg(
        session,
        "Beyond window",
        application_date=today + timedelta(days=2000),
    )
    session.commit()

    svc = DeadlineService(session)
    items = svc.upcoming(window_days=400)

    refs = [d.reference_number for d in items]
    assert "Near" in refs
    assert "Far" in refs
    assert "Beyond window" not in refs

    near = next(d for d in items if d.reference_number == "Near")
    far = next(d for d in items if d.reference_number == "Far")
    assert near.kind == "TRANSPOSITION"
    assert far.kind == "APPLICATION"
    assert near.severity_band == "RED"
    assert far.severity_band == "BLUE"
    # Sorted ascending by days_until.
    assert items.index(near) < items.index(far)
    # done defaults to False
    assert near.done is False
    assert far.done is False


def test_upcoming_includes_overdue(tmp_path: Path) -> None:
    session = _session(tmp_path)
    today = date.today()
    _add_reg(
        session,
        "Past",
        transposition_deadline=today - timedelta(days=5),
    )
    session.commit()

    svc = DeadlineService(session)
    items = svc.upcoming(window_days=365)
    assert any(
        d.reference_number == "Past" and d.severity_band == "OVERDUE"
        for d in items
    )


def test_upcoming_hides_done_by_default(tmp_path: Path) -> None:
    session = _session(tmp_path)
    today = date.today()
    _add_reg(
        session,
        "DoneReg",
        transposition_deadline=today + timedelta(days=20),
        transposition_done=True,
    )
    _add_reg(
        session,
        "ActiveReg",
        transposition_deadline=today + timedelta(days=50),
    )
    session.commit()

    svc = DeadlineService(session)
    items = svc.upcoming(window_days=365)
    refs = [d.reference_number for d in items]
    assert "DoneReg" not in refs
    assert "ActiveReg" in refs


def test_upcoming_show_completed_includes_done(tmp_path: Path) -> None:
    session = _session(tmp_path)
    today = date.today()
    _add_reg(
        session,
        "DoneReg",
        application_date=today + timedelta(days=30),
        application_done=True,
    )
    session.commit()

    svc = DeadlineService(session)
    items = svc.upcoming(window_days=365, show_completed=True)
    found = next((d for d in items if d.reference_number == "DoneReg"), None)
    assert found is not None
    assert found.done is True


def test_set_done_transposition(tmp_path: Path) -> None:
    session = _session(tmp_path)
    today = date.today()
    _add_reg(
        session,
        "SetDoneReg",
        transposition_deadline=today + timedelta(days=10),
    )
    session.commit()

    svc = DeadlineService(session)
    # Verify it appears before marking done
    items_before = svc.upcoming(window_days=365)
    target = next(d for d in items_before if d.reference_number == "SetDoneReg")
    reg_id = target.regulation_id

    svc.set_done(reg_id, "TRANSPOSITION", done=True)
    session.commit()

    # Should be hidden now
    items_after = svc.upcoming(window_days=365)
    assert not any(d.regulation_id == reg_id for d in items_after)

    # Should appear with show_completed=True
    items_completed = svc.upcoming(window_days=365, show_completed=True)
    found = next(d for d in items_completed if d.regulation_id == reg_id)
    assert found.done is True


def test_set_done_restore(tmp_path: Path) -> None:
    session = _session(tmp_path)
    today = date.today()
    _add_reg(
        session,
        "RestoreReg",
        application_date=today + timedelta(days=10),
        application_done=True,
    )
    session.commit()

    svc = DeadlineService(session)
    items_hidden = svc.upcoming(window_days=365)
    assert not any(d.reference_number == "RestoreReg" for d in items_hidden)

    # Get the reg_id via show_completed
    items_all = svc.upcoming(window_days=365, show_completed=True)
    reg_id = next(d for d in items_all if d.reference_number == "RestoreReg").regulation_id

    svc.set_done(reg_id, "APPLICATION", done=False)
    session.commit()

    items_restored = svc.upcoming(window_days=365)
    found = next(d for d in items_restored if d.regulation_id == reg_id)
    assert found.done is False


def test_set_done_invalid_regulation(tmp_path: Path) -> None:
    session = _session(tmp_path)
    Base.metadata.create_all(session.bind)  # type: ignore[arg-type]
    svc = DeadlineService(session)
    try:
        svc.set_done(99999, "TRANSPOSITION", done=True)
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "99999" in str(exc)
