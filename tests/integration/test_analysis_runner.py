from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from regwatch.analysis.runner import AnalysisRunner
from regwatch.db.extraction_field_seed import seed_core_fields
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


def _seed_one(sf) -> int:
    with sf() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
            title="Risk mgmt", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="This circular addresses ICT risk management.",
        )
        s.add(v); s.flush()
        seed_core_fields(s)
        s.commit()
        return v.version_id


def test_runner_runs_and_persists_analysis():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    version_id = _seed_one(sf)

    llm = MagicMock()
    llm.chat.return_value = (
        '{"main_points": "- ICT risk.", "is_ict": true, '
        '"document_relationship": "NEW", "keywords": ["ICT"]}'
    )

    runner = AnalysisRunner(session_factory=sf, llm=llm, max_document_tokens=5000)
    run_id = runner.queue_and_run([version_id], triggered_by="USER_CLI", llm_model="t")

    with sf() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status is AnalysisRunStatus.SUCCESS
        analyses = s.query(DocumentAnalysis).all()
        assert len(analyses) == 1
        a = analyses[0]
        assert a.status is DocumentAnalysisStatus.SUCCESS
        assert a.is_ict is True
        # Writeback applied
        reg = s.get(Regulation, a.regulation_id)
        assert reg.is_ict is True


def test_runner_marks_partial_on_mixed_failures():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    good = _seed_one(sf)
    # Seed a second version with a different regulation
    with sf() as s:
        reg2 = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 20/759",
            title="Other", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg2); s.flush()
        v2 = DocumentVersion(
            regulation_id=reg2.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h2",
            pdf_extracted_text="",  # blank text -> will fail analysis
        )
        s.add(v2); s.flush()
        s.commit()
        bad = v2.version_id

    llm = MagicMock()
    llm.chat.side_effect = ['{"is_ict": true}', Exception("LLM timeout")]

    runner = AnalysisRunner(session_factory=sf, llm=llm, max_document_tokens=5000)
    run_id = runner.queue_and_run([good, bad], triggered_by="USER_CLI", llm_model="t")

    with sf() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status is AnalysisRunStatus.PARTIAL
        statuses = sorted(
            a.status.value for a in s.query(DocumentAnalysis).all()
        )
        assert statuses == ["FAILED", "SUCCESS"]


def test_runner_persists_coercion_errors():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    version_id = _seed_one(sf)

    llm = MagicMock()
    llm.chat.return_value = (
        '{"is_ict": true, "implementation_deadline": "Q3 2026", '
        '"keywords": ["ICT"]}'
    )
    runner = AnalysisRunner(session_factory=sf, llm=llm, max_document_tokens=5000)
    runner.queue_and_run([version_id], triggered_by="USER_CLI", llm_model="t")

    with sf() as s:
        a = s.query(DocumentAnalysis).one()
        assert a.status is DocumentAnalysisStatus.SUCCESS
        assert a.implementation_deadline is None
        assert a.coercion_errors is not None
        assert "implementation_deadline" in a.coercion_errors


def test_runner_stops_between_documents_when_aborted():
    """The status bar's Abort button set a flag the analysis runner never
    checked; an Analyse run always went on to the last document."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    first = _seed_one(sf)
    with sf() as s:
        v = s.get(DocumentVersion, first)
        second = DocumentVersion(
            regulation_id=v.regulation_id, version_number=2, is_current=False,
            fetched_at=datetime.now(UTC), source_url="y", content_hash="h2",
            pdf_extracted_text="Second text.",
        )
        s.add(second)
        s.commit()
        second_id = second.version_id

    llm = MagicMock()
    llm.chat.return_value = '{"main_points": "- x", "is_ict": false}'
    stop = {"now": False}

    def _progress(done: int, total: int, label: str) -> None:
        stop["now"] = done >= 1  # abort requested while the first one runs

    runner = AnalysisRunner(
        session_factory=sf, llm=llm, max_document_tokens=5000,
        on_progress=_progress, should_stop=lambda: stop["now"],
    )
    run_id = runner.queue_and_run([first, second_id], triggered_by="USER_UI", llm_model="t")

    with sf() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status is AnalysisRunStatus.ABORTED
        assert "Aborted after 1 of 2" in (run.error_summary or "")
        assert [a.version_id for a in s.query(DocumentAnalysis).all()] == [first]
