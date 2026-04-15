"""RegulationOverride action-value coverage."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from regwatch.db.models import Base, RegulationOverride


def test_keep_active_action_persists():
    """KEEP_ACTIVE is a newly documented action value; ensure it round-trips."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        ov = RegulationOverride(
            reference_number="CSSF 22/806",
            action="KEEP_ACTIVE",
            reason="Manual keep — user flagged this as still-relevant.",
            created_at=datetime.now(UTC),
        )
        s.add(ov)
        s.commit()

        got = s.scalars(select(RegulationOverride)).one()
        assert got.action == "KEEP_ACTIVE"
        assert got.reference_number == "CSSF 22/806"
