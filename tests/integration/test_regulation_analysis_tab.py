from datetime import UTC, datetime

from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def test_analysis_tab_shows_latest_analysis(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 12/552",
            title="Risk mgmt",
            issuing_authority="CSSF",
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
            pdf_extracted_text="t",
        )
        s.add(v)
        s.flush()
        seed_core_fields(s)
        s.flush()
        run = AnalysisRun(
            status=AnalysisRunStatus.SUCCESS,
            queued_version_ids=[v.version_id],
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            llm_model="t",
            triggered_by="USER_UI",
        )
        s.add(run)
        s.flush()
        a = DocumentAnalysis(
            run_id=run.run_id,
            version_id=v.version_id,
            regulation_id=reg.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            is_ict=True,
            main_points="- point A\n- point B",
            keywords=["ICT", "DORA"],
            raw_llm_output='{"main_points": "- point A\\n- point B"}',
        )
        s.add(a)
        s.commit()
        rid = reg.regulation_id

    r = c.get(f"/regulations/{rid}")
    assert r.status_code == 200
    assert "point A" in r.text
    assert "Analysis" in r.text
    assert "ICT" in r.text  # keyword appears


def test_analysis_tab_shows_not_analysed_yet_when_empty(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 20/759",
            title="Other",
            issuing_authority="CSSF",
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
        s.commit()
        rid = reg.regulation_id

    r = c.get(f"/regulations/{rid}")
    assert r.status_code == 200
    text_lower = r.text.lower()
    assert "not analysed" in text_lower or "no analysis" in text_lower or "analyse" in text_lower
