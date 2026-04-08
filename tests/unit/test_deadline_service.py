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
