from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import Base, DiscoveryRun, DiscoveryRunItem


def test_discovery_run_round_trip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        run = DiscoveryRun(
            status="RUNNING",
            started_at=datetime.now(UTC),
            triggered_by="USER_UI",
            entity_types=["AIFM", "CHAPTER15_MANCO"],
            mode="incremental",
        )
        s.add(run)
        s.flush()
        item = DiscoveryRunItem(
            run_id=run.run_id,
            regulation_id=None,
            reference_number="CSSF 22/806",
            outcome="NEW",
            detail_url="https://example.test/circular-cssf-22-806/",
            entity_types=["AIFM"],
            note="first time scraped",
        )
        s.add(item)
        s.commit()

        got = s.query(DiscoveryRun).one()
        assert got.status == "RUNNING"
        assert got.entity_types == ["AIFM", "CHAPTER15_MANCO"]
        assert len(got.items) == 1
        assert got.items[0].outcome == "NEW"
        assert got.items[0].run.run_id == run.run_id  # relationship traversal
