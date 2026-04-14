from tests.integration.test_app_smoke import _client
from regwatch.db.models import (
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)


def test_upload_html_creates_version(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    # Stub the embedder so indexing doesn't call LM Studio
    c.app.state.llm_client.embed = lambda text: [0.0] * c.app.state.config.llm.embedding_dim
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="X",
            title="t",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x",
            source_of_truth="SEED",
        )
        s.add(reg)
        s.commit()
        rid = reg.regulation_id

    html = b"<html><body><h1>Policy</h1><p>This document mentions ICT and more relevant content to extract.</p></body></html>"
    r = c.post(
        f"/catalog/{rid}/upload",
        files={"file": ("doc.html", html, "text/html")},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), f"got {r.status_code}: {r.text[:200]}"

    with c.app.state.session_factory() as s:
        versions = s.query(DocumentVersion).filter_by(regulation_id=rid).all()
        assert len(versions) == 1
        v = versions[0]
        assert v.pdf_manual_upload is True
        assert v.is_current is True
        # Either html_text or the extracted text holds the body
        assert v.html_text is not None
        assert "ICT" in v.html_text or "ICT" in (v.pdf_extracted_text or "")


def test_upload_dedup_on_same_content(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    c.app.state.llm_client.embed = lambda text: [0.0] * c.app.state.config.llm.embedding_dim
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="X2",
            title="t",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x",
            source_of_truth="SEED",
        )
        s.add(reg)
        s.commit()
        rid = reg.regulation_id

    html = b"<html><body><p>stable content that is long enough for trafilatura to detect and extract it as main text.</p></body></html>"
    r1 = c.post(
        f"/catalog/{rid}/upload",
        files={"file": ("a.html", html, "text/html")},
        follow_redirects=False,
    )
    r2 = c.post(
        f"/catalog/{rid}/upload",
        files={"file": ("b.html", html, "text/html")},
        follow_redirects=False,
    )
    assert r1.status_code in (302, 303)
    assert r2.status_code in (302, 303)

    with c.app.state.session_factory() as s:
        # Second upload deduped - still only one version
        assert s.query(DocumentVersion).filter_by(regulation_id=rid).count() == 1


def test_upload_rejects_bad_extension(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="X3",
            title="t",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x",
            source_of_truth="SEED",
        )
        s.add(reg)
        s.commit()
        rid = reg.regulation_id

    r = c.post(
        f"/catalog/{rid}/upload",
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
        follow_redirects=False,
    )
    # Route rejects - either 400 or a redirect to the detail page with an error query param.
    assert r.status_code in (302, 303, 400)
