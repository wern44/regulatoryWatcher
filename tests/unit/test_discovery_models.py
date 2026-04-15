from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import (
    Base,
    DiscoveryRun,
    DiscoveryRunItem,
    LifecycleStage,
    Regulation,
    RegulationDiscoverySource,
    RegulationType,
)


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


def test_regulation_discovery_source_round_trip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 22/806",
            title="X",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        )
        s.add(reg)
        s.flush()
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            triggered_by="USER_CLI",
            entity_types=["AIFM"],
            mode="full",
        )
        s.add(run)
        s.flush()
        src = RegulationDiscoverySource(
            regulation_id=reg.regulation_id,
            entity_type="AIFM",
            content_type="circulars-cssf",
            first_seen_run_id=run.run_id,
            first_seen_at=datetime.now(UTC),
            last_seen_run_id=run.run_id,
            last_seen_at=datetime.now(UTC),
        )
        s.add(src)
        s.commit()
        assert src.source_id is not None
        got = s.query(RegulationDiscoverySource).one()
        assert got.entity_type == "AIFM"
        assert got.content_type == "circulars-cssf"
        assert got.regulation_id == reg.regulation_id


def test_regulation_discovery_source_unique_constraint():
    """(regulation_id, entity_type, content_type) must be unique."""
    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 22/806",
            title="X",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            needs_review=False,
            url="",
            source_of_truth="CSSF_WEB",
        )
        s.add(reg)
        s.flush()
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            triggered_by="USER_CLI",
            entity_types=["AIFM"],
            mode="full",
        )
        s.add(run)
        s.flush()
        now = datetime.now(UTC)
        s.add(RegulationDiscoverySource(
            regulation_id=reg.regulation_id,
            entity_type="AIFM", content_type="circulars-cssf",
            first_seen_run_id=run.run_id, first_seen_at=now,
            last_seen_run_id=run.run_id, last_seen_at=now,
        ))
        s.commit()
        s.add(RegulationDiscoverySource(
            regulation_id=reg.regulation_id,
            entity_type="AIFM", content_type="circulars-cssf",
            first_seen_run_id=run.run_id, first_seen_at=now,
            last_seen_run_id=run.run_id, last_seen_at=now,
        ))
        try:
            s.commit()
            raised = False
        except IntegrityError:
            raised = True
        assert raised, "expected IntegrityError on duplicate (reg, entity, content_type)"


def test_discovery_run_retired_count_defaults_zero():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            triggered_by="USER_CLI",
            entity_types=[],
            mode="full",
        )
        s.add(run)
        s.commit()
        assert run.retired_count == 0
