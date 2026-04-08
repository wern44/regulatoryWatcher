from datetime import datetime, timezone
from pathlib import Path

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationType,
    UpdateEvent,
)
from tests.integration.test_app_smoke import _client


def _seed(db_file: Path) -> None:
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        def add(ref: str, *, is_ict: bool, stage: LifecycleStage) -> None:
            reg = Regulation(
                type=RegulationType.CSSF_CIRCULAR,
                reference_number=ref,
                title=ref,
                issuing_authority="CSSF",
                lifecycle_stage=stage,
                is_ict=is_ict,
                source_of_truth="SEED",
                url="https://example.com",
            )
            reg.applicabilities.append(
                RegulationApplicability(authorization_type="BOTH")
            )
            session.add(reg)

        add("R1", is_ict=False, stage=LifecycleStage.IN_FORCE)
        add("R2", is_ict=False, stage=LifecycleStage.IN_FORCE)
        add("R3", is_ict=False, stage=LifecycleStage.PROPOSAL)
        add("DORA", is_ict=True, stage=LifecycleStage.IN_FORCE)

        ev = UpdateEvent(
            source="cssf_rss",
            source_url="https://example.com/e",
            title="New event",
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
            raw_payload={},
            content_hash="h" * 64,
            is_ict=False,
            severity="MATERIAL",
            review_status="NEW",
        )
        session.add(ev)
        session.commit()


def test_dashboard_shows_kpi_counts(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed(tmp_path / "app.db")

    response = client.get("/")
    assert response.status_code == 200
    text = response.text
    assert "Dashboard" in text
    # 3 in-force regulations (R1, R2, DORA).
    assert 'data-kpi="catalog">3<' in text
    # 1 new inbox event.
    assert 'data-kpi="inbox">1<' in text
    # 1 draft (R3 in PROPOSAL).
    assert 'data-kpi="drafts">1<' in text
    # 1 ICT (DORA).
    assert 'data-kpi="ict">1<' in text
