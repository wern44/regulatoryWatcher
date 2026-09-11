"""AmendmentIndex: rolls amendments up under the regulation they amend."""
from datetime import date
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
from regwatch.services.regulations import (
    AmendmentIndex,
    RegulationFilter,
    RegulationService,
)


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add(
    session: Session, ref: str, published: date | None, *, is_ict: bool = False
) -> Regulation:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=ref,
        title=ref,
        issuing_authority="CSSF",
        publication_date=published,
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=is_ict,
        source_of_truth="SEED",
        url="https://example.com",
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


def test_fold_hides_amendments_of_a_listed_regulation(tmp_path: Path) -> None:
    session = _session(tmp_path)
    parent = _add(session, "CSSF 20/750", date(2020, 8, 31))
    child = _add(session, "CSSF 22/806", date(2022, 4, 22))
    _amends(session, child, parent)
    session.commit()

    regs = RegulationService(session).list(RegulationFilter())
    folded = AmendmentIndex(session).fold(regs)

    assert [r.reference_number for r in folded] == ["CSSF 20/750"]


def test_fold_keeps_an_amendment_whose_parent_is_not_listed(tmp_path: Path) -> None:
    """An ICT amendment of a non-ICT circular must stay visible on the ICT
    page — it has no listed parent to be rolled up under."""
    session = _session(tmp_path)
    parent = _add(session, "CSSF 18/698", date(2018, 8, 23))
    child = _add(session, "CSSF 22/811", date(2022, 5, 16), is_ict=True)
    _amends(session, child, parent)
    session.commit()

    regs = RegulationService(session).list(RegulationFilter(is_ict=True))
    folded = AmendmentIndex(session).fold(regs)

    assert [r.reference_number for r in folded] == ["CSSF 22/811"]


def test_summary_gives_count_and_newest_amendment_date(tmp_path: Path) -> None:
    """Chained amendments count towards the top-level circular; the last
    change is the newest amendment's publication date."""
    session = _session(tmp_path)
    parent = _add(session, "CSSF 20/750", date(2020, 8, 31))
    first = _add(session, "CSSF 22/806", date(2022, 4, 22))
    second = _add(session, "CSSF 24/900", date(2024, 3, 1))
    undated = _add(session, "CSSF 25/901", None)
    _amends(session, first, parent)
    _amends(session, second, first)
    _amends(session, undated, parent)
    session.commit()

    regs = RegulationService(session).list(RegulationFilter())
    index = AmendmentIndex(session)
    summary = index.summaries(index.fold(regs))[parent.regulation_id]

    assert summary.count == 3
    assert summary.last_change == date(2024, 3, 1)
    assert summary.last_change_reference == "CSSF 24/900"


def test_summary_without_amendments_uses_own_publication_date(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add(session, "CSSF 18/698", date(2018, 8, 23))
    session.commit()

    regs = RegulationService(session).list(RegulationFilter())
    summary = AmendmentIndex(session).summaries(regs)[reg.regulation_id]

    assert summary.count == 0
    assert summary.last_change == date(2018, 8, 23)
    assert summary.last_change_reference is None
