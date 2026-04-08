from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationType,
)
from regwatch.services.regulations import RegulationFilter, RegulationService


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session) -> None:
    def add(ref: str, auth: str, is_ict: bool, stage: LifecycleStage) -> None:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number=ref,
            title=ref,
            issuing_authority="CSSF",
            lifecycle_stage=stage,
            is_ict=is_ict,
            source_of_truth="SEED",
            url="https://example.com",
        )
        reg.applicabilities.append(
            RegulationApplicability(authorization_type=auth)
        )
        session.add(reg)

    add("CSSF 18/698", "BOTH", False, LifecycleStage.IN_FORCE)
    add("CSSF 23/844", "AIFM", False, LifecycleStage.IN_FORCE)
    add("CSSF 11/512", "CHAPTER15_MANCO", False, LifecycleStage.IN_FORCE)
    add("DORA", "BOTH", True, LifecycleStage.IN_FORCE)
    add("AIFMD II", "BOTH", False, LifecycleStage.ADOPTED_NOT_IN_FORCE)
    session.commit()


def test_list_all(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed(session)
    svc = RegulationService(session)
    assert len(svc.list(RegulationFilter())) == 5


def test_filter_by_aifm_includes_both(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed(session)
    svc = RegulationService(session)
    aifm = svc.list(RegulationFilter(authorization_type="AIFM"))
    refs = {r.reference_number for r in aifm}
    assert "CSSF 18/698" in refs  # BOTH
    assert "CSSF 23/844" in refs  # AIFM
    assert "CSSF 11/512" not in refs  # MANCO-only


def test_filter_by_is_ict(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed(session)
    svc = RegulationService(session)
    ict = svc.list(RegulationFilter(is_ict=True))
    assert len(ict) == 1
    assert ict[0].reference_number == "DORA"


def test_get_by_reference(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed(session)
    svc = RegulationService(session)
    reg = svc.get_by_reference("CSSF 18/698")
    assert reg is not None
    assert reg.title == "CSSF 18/698"
