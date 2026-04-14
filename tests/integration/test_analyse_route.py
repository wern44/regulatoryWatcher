import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import (
    AnalysisRun,
    DocumentAnalysis,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed(c):
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
            title="t", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="ICT content here.",
        )
        s.add(v); seed_core_fields(s); s.commit()
        return reg.regulation_id, v.version_id


def test_catalog_analyse_queues_and_runs(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    fake_llm = MagicMock()
    fake_llm.chat.return_value = '{"is_ict": true, "keywords": ["ICT"]}'
    fake_llm.chat_model = "mock"
    c.app.state.llm_client = fake_llm
    reg_id, _ = _seed(c)

    r = c.post(
        "/catalog/analyse",
        data={"regulation_ids": [str(reg_id)]},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert "/analysis/runs/" in r.headers["location"]

    # Wait for the worker thread to complete
    for _ in range(50):
        with c.app.state.session_factory() as s:
            if s.query(DocumentAnalysis).count() > 0:
                break
        time.sleep(0.1)
    else:
        raise AssertionError("Analysis did not complete within timeout")

    with c.app.state.session_factory() as s:
        # Exactly one successful run, one analysis
        assert s.query(AnalysisRun).count() == 1
        assert s.query(DocumentAnalysis).count() == 1
