from regwatch.db.models import LifecycleStage, Regulation, RegulationType
from tests.integration.test_app_smoke import _client


def test_catalog_page_has_checkboxes_and_analyse_form(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    # Seed one regulation so a row (with checkbox) renders.
    with c.app.state.session_factory() as s:
        s.add(
            Regulation(
                reference_number="CSSF 12/552",
                type=RegulationType.CSSF_CIRCULAR,
                title="Test Circular",
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=False,
                url="https://example.invalid/1",
                source_of_truth="MANUAL",
                needs_review=False,
            )
        )
        s.commit()

    r = c.get("/catalog")
    assert r.status_code == 200
    # Checkbox input for regulation selection
    assert 'name="regulation_ids"' in r.text
    # Form targets the analyse endpoint
    assert 'action="/catalog/analyse"' in r.text
