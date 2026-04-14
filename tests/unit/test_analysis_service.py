from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    Base,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.services.analysis import AnalysisService


def test_latest_analysis_for_regulation_returns_newest():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="x",
            title="t",
            issuing_authority="x",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x",
            source_of_truth="SEED",
        )
        s.add(reg)
        s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id,
            version_number=1,
            is_current=True,
            fetched_at=datetime.now(UTC),
            source_url="x",
            content_hash="h",
        )
        s.add(v)
        s.flush()

        for i in range(2):
            run = AnalysisRun(
                status=AnalysisRunStatus.SUCCESS,
                queued_version_ids=[v.version_id],
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                llm_model="t",
                triggered_by="USER_CLI",
            )
            s.add(run)
            s.flush()
            a = DocumentAnalysis(
                run_id=run.run_id,
                version_id=v.version_id,
                regulation_id=reg.regulation_id,
                status=DocumentAnalysisStatus.SUCCESS,
                is_ict=bool(i),
            )
            s.add(a)
            s.commit()

        svc = AnalysisService(s)
        dto = svc.latest_for_regulation(reg.regulation_id)
        assert dto is not None
        assert dto.is_ict is True  # second run won


def test_get_run_returns_none_for_missing():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        svc = AnalysisService(s)
        assert svc.get_run(9999) is None


def test_get_run_returns_dto_with_nested_analyses():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="x", title="t",
            issuing_authority="x", lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
        )
        s.add(v); s.flush()
        run = AnalysisRun(
            status=AnalysisRunStatus.SUCCESS, queued_version_ids=[v.version_id],
            started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            llm_model="m", triggered_by="USER_CLI",
        )
        s.add(run); s.flush()
        # Create 3 more versions so we can attach 3 analyses under the same run
        # (unique constraint on (run_id, version_id) forbids duplicates per version).
        version_ids = [v.version_id]
        for n in range(2, 5):
            vx = DocumentVersion(
                regulation_id=reg.regulation_id, version_number=n, is_current=False,
                fetched_at=datetime.now(UTC), source_url="x", content_hash=f"h{n}",
            )
            s.add(vx); s.flush()
            version_ids.append(vx.version_id)
        for i, vid in enumerate(version_ids[:3]):
            s.add(DocumentAnalysis(
                run_id=run.run_id, version_id=vid, regulation_id=reg.regulation_id,
                status=DocumentAnalysisStatus.SUCCESS, is_ict=bool(i % 2),
            ))
        s.commit()

        dto = AnalysisService(s).get_run(run.run_id)
        assert dto is not None
        assert dto.run_id == run.run_id
        assert dto.status == "SUCCESS"
        assert dto.llm_model == "m"
        assert dto.triggered_by == "USER_CLI"
        assert len(dto.analyses) == 3
        assert {a.version_id for a in dto.analyses} == set(version_ids[:3])
