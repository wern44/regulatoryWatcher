from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed(db_file: Path) -> int:
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 18/698",
            title="IFM",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            source_of_truth="SEED",
            url="https://example.com",
        )
        session.add(reg)
        session.flush()

        v = DocumentVersion(
            regulation_id=reg.regulation_id,
            version_number=1,
            is_current=True,
            fetched_at=datetime.now(timezone.utc),
            source_url="https://example.com",
            content_hash="1" * 64,
            html_text="body",
            pdf_is_protected=False,
            pdf_manual_upload=False,
            change_summary="-old\n+new\n",
        )
        session.add(v)
        session.commit()
        return reg.regulation_id


def test_regulation_detail_view(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    rid = _seed(tmp_path / "app.db")

    r = client.get(f"/regulations/{rid}")
    assert r.status_code == 200
    assert "CSSF 18/698" in r.text
    assert "v1" in r.text
    assert "IFM" in r.text


def test_regulation_detail_404(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    r = client.get("/regulations/999999")
    assert r.status_code == 404
