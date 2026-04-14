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
