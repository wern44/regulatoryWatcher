from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.analysis.writeback import apply_writeback
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    Base,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationOverride,
    RegulationType,
)


def _seed(s: Session, *, reference: str = "CSSF 12/552") -> tuple[Regulation, DocumentVersion]:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR, reference_number=reference,
        title="Test", issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        is_ict=False,
    )
    s.add(reg)
    s.flush()
    v = DocumentVersion(
        regulation_id=reg.regulation_id, version_number=1, is_current=True,
        fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
    )
    s.add(v)
    s.flush()
    return reg, v


def _make_run(s: Session, version_id: int) -> AnalysisRun:
    run = AnalysisRun(
        status=AnalysisRunStatus.RUNNING, queued_version_ids=[version_id],
        started_at=datetime.now(UTC), llm_model="test", triggered_by="USER_CLI",
    )
    s.add(run)
    s.flush()
    return run


def test_writeback_updates_is_ict_and_entity_types():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg, v = _seed(s)
        run = _make_run(s, v.version_id)
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=v.version_id, regulation_id=reg.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            is_ict=True, applicable_entity_types=["AIFM", "CHAPTER15_MANCO"],
            document_relationship="NEW",
        )
        s.add(a)
        s.flush()
        apply_writeback(s, a)
        s.refresh(reg)
        assert reg.is_ict is True
        assert reg.applicable_entity_types == ["AIFM", "CHAPTER15_MANCO"]


def test_writeback_respects_set_ict_override():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg, v = _seed(s)
        s.add(RegulationOverride(
            regulation_id=reg.regulation_id, reference_number=reg.reference_number,
            action="UNSET_ICT", created_at=datetime.now(UTC),
        ))
        s.flush()
        run = _make_run(s, v.version_id)
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=v.version_id, regulation_id=reg.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS, is_ict=True,
        )
        s.add(a); s.flush()
        apply_writeback(s, a)
        s.refresh(reg)
        assert reg.is_ict is False  # override wins


def test_writeback_replaces_sets_replaced_by_id():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        old, _ = _seed(s, reference="CSSF 11/498")
        new, v = _seed(s, reference="CSSF 12/552")
        run = _make_run(s, v.version_id)
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=v.version_id, regulation_id=new.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            document_relationship="REPLACES", relationship_target="CSSF 11/498",
        )
        s.add(a); s.flush()
        apply_writeback(s, a)
        s.refresh(old)
        assert old.replaced_by_id == new.regulation_id


def test_writeback_deadline_routes_to_transposition_for_eu_directive():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.EU_DIRECTIVE, reference_number="DORA",
            celex_id="32022L2556", title="DORA", issuing_authority="EU",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
            is_ict=False,
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
        )
        s.add(v); s.flush()
        run = _make_run(s, v.version_id)
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=v.version_id, regulation_id=reg.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            implementation_deadline=date(2025, 1, 17),
            document_relationship="NEW",
        )
        s.add(a); s.flush()
        apply_writeback(s, a)
        s.refresh(reg)
        assert reg.transposition_deadline == date(2025, 1, 17)
        assert reg.application_date is None
