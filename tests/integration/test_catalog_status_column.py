from datetime import UTC, datetime

from tests.integration.test_app_smoke import _client
from regwatch.db.models import (
    AnalysisRun, AnalysisRunStatus, DocumentAnalysis, DocumentAnalysisStatus,
    DocumentVersion, LifecycleStage, Regulation, RegulationType,
)


def _mk_reg(s, ref):
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR, reference_number=ref,
        title="T", issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
    )
    s.add(reg); s.flush()
    return reg


def _mk_version(s, reg, *, number, current):
    v = DocumentVersion(
        regulation_id=reg.regulation_id, version_number=number, is_current=current,
        fetched_at=datetime.now(UTC), source_url="x",
        content_hash=f"h{reg.reference_number}{number}",
    )
    s.add(v); s.flush()
    return v


def _mk_analysis(s, run, version, reg, *, status):
    a = DocumentAnalysis(
        run_id=run.run_id, version_id=version.version_id,
        regulation_id=reg.regulation_id, status=status,
    )
    s.add(a); s.flush()
    return a


def test_catalog_shows_analysis_status_per_row(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    with c.app.state.session_factory() as s:
        # never analysed
        never = _mk_reg(s, "CSSF 1/000")
        _mk_version(s, never, number=1, current=True)

        # ok: analysed and current
        ok = _mk_reg(s, "CSSF 2/000")
        v_ok = _mk_version(s, ok, number=1, current=True)
        run_ok = AnalysisRun(
            status=AnalysisRunStatus.SUCCESS, queued_version_ids=[v_ok.version_id],
            started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            llm_model="t", triggered_by="USER_UI",
        )
        s.add(run_ok); s.flush()
        _mk_analysis(s, run_ok, v_ok, ok, status=DocumentAnalysisStatus.SUCCESS)

        # stale: analysed old version, newer current version exists with no analysis
        stale = _mk_reg(s, "CSSF 3/000")
        v_old = _mk_version(s, stale, number=1, current=False)
        v_new = _mk_version(s, stale, number=2, current=True)
        run_stale = AnalysisRun(
            status=AnalysisRunStatus.SUCCESS, queued_version_ids=[v_old.version_id],
            started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            llm_model="t", triggered_by="USER_UI",
        )
        s.add(run_stale); s.flush()
        _mk_analysis(s, run_stale, v_old, stale, status=DocumentAnalysisStatus.SUCCESS)

        # failed: latest analysis failed
        failed = _mk_reg(s, "CSSF 4/000")
        v_failed = _mk_version(s, failed, number=1, current=True)
        run_failed = AnalysisRun(
            status=AnalysisRunStatus.FAILED, queued_version_ids=[v_failed.version_id],
            started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            llm_model="t", triggered_by="USER_UI",
        )
        s.add(run_failed); s.flush()
        _mk_analysis(s, run_failed, v_failed, failed, status=DocumentAnalysisStatus.FAILED)

        s.commit()

    r = c.get("/catalog")
    assert r.status_code == 200
    t = r.text
    # Presence of status column header
    assert "Analysis" in t  # column header
    # Indicator strings — exact wording is flexible, just confirm the 4 regulations
    # each have distinct indicators. Look for human-readable markers.
    assert t.count("never") + t.count("—") >= 1  # never-analysed marker
    assert "stale" in t.lower() or "re-analyse" in t.lower()
    assert "failed" in t.lower() or "FAILED" in t
