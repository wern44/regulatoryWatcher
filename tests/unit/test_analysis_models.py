from datetime import UTC, datetime, date

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


def _seed_regulation_and_version(s: Session) -> DocumentVersion:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 12/552",
        title="Test circular",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        url="https://example.test/c.pdf",
        source_of_truth="SEED",
    )
    s.add(reg)
    s.flush()
    v = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=1,
        is_current=True,
        fetched_at=datetime.now(UTC),
        source_url="https://example.test/c.pdf",
        content_hash="abc",
    )
    s.add(v)
    s.flush()
    return v


def test_analysis_run_and_document_analysis_round_trip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        v = _seed_regulation_and_version(s)

        run = AnalysisRun(
            status=AnalysisRunStatus.SUCCESS,
            queued_version_ids=[v.version_id],
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            llm_model="qwen2.5-32b",
            triggered_by="USER_UI",
        )
        s.add(run)
        s.flush()

        a = DocumentAnalysis(
            run_id=run.run_id,
            version_id=v.version_id,
            regulation_id=v.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            raw_llm_output='{"is_ict": true}',
            was_truncated=False,
            is_ict=True,
            implementation_deadline=date(2026, 1, 17),
            document_relationship="NEW",
            keywords=["ICT", "DORA"],
            custom_fields={"severity": "high"},
        )
        s.add(a)
        s.commit()

        got = s.query(DocumentAnalysis).one()
        assert got.is_ict is True
        assert got.keywords == ["ICT", "DORA"]
        assert got.custom_fields == {"severity": "high"}
        assert got.run.status is AnalysisRunStatus.SUCCESS


def test_document_analysis_stores_coercion_errors_and_nullable_regulation_id():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from regwatch.db.models import (
        AnalysisRun, AnalysisRunStatus, Base, DocumentAnalysis, DocumentAnalysisStatus,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        run = AnalysisRun(
            status=AnalysisRunStatus.FAILED, queued_version_ids=[],
            started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            llm_model="t", triggered_by="USER_CLI",
        )
        s.add(run); s.flush()
        # regulation_id now nullable
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=42, regulation_id=None,
            status=DocumentAnalysisStatus.FAILED,
            error_detail="version gone",
            coercion_errors={"implementation_deadline": "ValueError: bad date"},
        )
        s.add(a); s.commit()
        got = s.query(DocumentAnalysis).one()
        assert got.regulation_id is None
        assert got.coercion_errors == {"implementation_deadline": "ValueError: bad date"}
