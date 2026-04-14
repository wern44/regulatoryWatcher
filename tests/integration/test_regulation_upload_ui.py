from tests.integration.test_app_smoke import _client
from regwatch.db.models import LifecycleStage, Regulation, RegulationType


def test_detail_page_has_upload_form(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="X",
            title="t", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.commit()
        rid = reg.regulation_id

    r = c.get(f"/regulations/{rid}")
    assert r.status_code == 200
    # The upload form POSTs multipart to /catalog/{id}/upload
    assert f'action="/catalog/{rid}/upload"' in r.text
    assert 'enctype="multipart/form-data"' in r.text
    assert 'type="file"' in r.text
    assert 'accept=".pdf,.html,.htm"' in r.text or 'accept=".pdf, .html, .htm"' in r.text


def test_detail_page_shows_upload_success_flash(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="Y",
            title="t", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.commit()
        rid = reg.regulation_id

    r = c.get(f"/regulations/{rid}?uploaded=1&version_id=99")
    assert r.status_code == 200
    # Some "upload success" indicator is shown
    t = r.text.lower()
    assert "uploaded" in t or "success" in t
