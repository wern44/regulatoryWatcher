from datetime import UTC, datetime

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


def _seed_run_with_analysis(c, status: AnalysisRunStatus) -> int:
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 12/552",
            title="Risk",
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
        s.flush()
        run = AnalysisRun(
            status=status,
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
        )
        s.add(a)
        s.commit()
        return run.run_id


def test_run_page_renders(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    run_id = _seed_run_with_analysis(c, AnalysisRunStatus.SUCCESS)
    r = c.get(f"/analysis/runs/{run_id}")
    assert r.status_code == 200
    assert f"Analysis run {run_id}" in r.text or f"run {run_id}" in r.text.lower()
    assert "Results" in r.text or "results" in r.text.lower()


def test_run_status_fragment_is_htmx_swappable(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    run_id = _seed_run_with_analysis(c, AnalysisRunStatus.SUCCESS)
    r = c.get(f"/analysis/runs/{run_id}/status")
    assert r.status_code == 200
    assert 'id="run-status"' in r.text
    assert "<html" not in r.text.lower()


def test_run_page_404_for_missing(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/analysis/runs/99999")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "not found" in r.text.lower()
