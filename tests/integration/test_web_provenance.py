"""Web UI provenance rendering: regulation detail + run detail."""
from __future__ import annotations

from datetime import UTC, datetime

from regwatch.db.models import (
    DiscoveryRun,
    DiscoveryRunItem,
    LifecycleStage,
    Regulation,
    RegulationDiscoverySource,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed_reg_with_sources(session_factory):
    with session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 99/TEST",
            title="Test Circular",
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
            triggered_by="TEST",
            entity_types=["AIFM"],
            mode="full",
        )
        s.add(run)
        s.flush()
        now = datetime.now(UTC)
        s.add_all([
            RegulationDiscoverySource(
                regulation_id=reg.regulation_id,
                entity_type="AIFM",
                content_type="CSSF circular",
                first_seen_run_id=run.run_id,
                first_seen_at=now,
                last_seen_run_id=run.run_id,
                last_seen_at=now,
            ),
            RegulationDiscoverySource(
                regulation_id=reg.regulation_id,
                entity_type="CHAPTER15_MANCO",
                content_type="CSSF circular",
                first_seen_run_id=run.run_id,
                first_seen_at=now,
                last_seen_run_id=run.run_id,
                last_seen_at=now,
            ),
        ])
        s.commit()
        return reg.regulation_id, run.run_id


def test_regulation_detail_renders_provenance(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sf = client.app.state.session_factory
    reg_id, _ = _seed_reg_with_sources(sf)

    resp = client.get(f"/regulations/{reg_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "Discovery provenance" in body
    assert "AIFM" in body
    assert "CHAPTER15_MANCO" in body
    assert "CSSF circular" in body


def test_run_detail_renders_cell_breakdown_and_retired_count(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sf = client.app.state.session_factory

    with sf() as s:
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            triggered_by="TEST",
            entity_types=["AIFM"],
            mode="full",
            retired_count=3,
        )
        s.add(run)
        s.flush()
        # Items in two distinct cells
        for ref, outcome, et, ct in [
            ("CSSF 99/X1", "NEW", "AIFM", "CSSF circular"),
            ("CSSF 99/X2", "UNCHANGED", "AIFM", "CSSF circular"),
            ("CSSF 99/X3", "NEW", "CHAPTER15_MANCO", "Law"),
        ]:
            s.add(DiscoveryRunItem(
                run_id=run.run_id,
                regulation_id=None,
                reference_number=ref,
                outcome=outcome,
                detail_url=None,
                entity_type=et,
                content_type=ct,
                note=None,
            ))
        s.commit()
        run_id = run.run_id

    resp = client.get(f"/discovery/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "Per-cell breakdown" in body
    assert "Retired" in body
    assert "3" in body
    # Both cells visible
    assert "Law" in body  # CHAPTER15_MANCO x Law cell
