"""Schema-level tests for the EntityType model."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from regwatch.db.models import Base, EntityType


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        yield s


def test_minimal_row_uses_defaults(session):
    et = EntityType(slug="AIFM", label="AIFM")
    session.add(et)
    session.commit()
    session.refresh(et)
    assert et.entity_type_id is not None
    assert et.active is True
    assert et.sort_order == 100
    assert et.cssf_entity_filter_id is None
    assert et.cssf_detail_labels is None


def test_slug_is_unique(session):
    session.add(EntityType(slug="AIFM", label="AIFM"))
    session.commit()
    session.add(EntityType(slug="AIFM", label="duplicate"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_cssf_detail_labels_roundtrip_as_json(session):
    labels = ["Alternative investment fund manager", "AIFM"]
    et = EntityType(
        slug="AIFM",
        label="AIFM",
        cssf_entity_filter_id=502,
        cssf_detail_labels=labels,
    )
    session.add(et)
    session.commit()
    session.expire_all()
    refetched = session.query(EntityType).filter_by(slug="AIFM").one()
    assert refetched.cssf_detail_labels == labels
    assert refetched.cssf_entity_filter_id == 502
