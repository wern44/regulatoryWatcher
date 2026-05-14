"""CRUD service for entity types."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from regwatch.db.entity_type_seed import seed_default_entity_types
from regwatch.db.models import (
    Base,
    EntityType,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationType,
)
from regwatch.services.entity_types import (
    EntityTypeService,
    InvalidSlugError,
    SlugConflictError,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()
        yield s


def test_list_active_orders_by_sort_then_slug(session):
    session.add(EntityType(slug="PSF_SUPPORT", label="PSF Support", sort_order=30))
    session.add(EntityType(slug="PSF_SPECIALISED", label="PSF Specialised", sort_order=30))
    session.commit()
    svc = EntityTypeService(session)
    rows = svc.list_active()
    slugs = [r.slug for r in rows]
    assert slugs == ["AIFM", "CHAPTER15_MANCO", "PSF_SPECIALISED", "PSF_SUPPORT"]


def test_list_active_hides_inactive_rows(session):
    svc = EntityTypeService(session)
    svc.deactivate(svc.get_by_slug("AIFM").entity_type_id)
    session.commit()
    slugs = [r.slug for r in svc.list_active()]
    assert "AIFM" not in slugs
    assert "CHAPTER15_MANCO" in slugs


def test_create_validates_slug_format(session):
    svc = EntityTypeService(session)
    with pytest.raises(InvalidSlugError):
        svc.create(slug="bad slug", label="x")
    with pytest.raises(InvalidSlugError):
        svc.create(slug="lower_case", label="x")
    with pytest.raises(InvalidSlugError):
        svc.create(slug="A", label="x")  # too short


def test_create_rejects_duplicate_slug(session):
    svc = EntityTypeService(session)
    with pytest.raises(SlugConflictError):
        svc.create(slug="AIFM", label="duplicate")


def test_create_accepts_valid_slug(session):
    svc = EntityTypeService(session)
    dto = svc.create(
        slug="PSF_SPECIALISED",
        label="PSF — Specialised",
        cssf_entity_filter_id=1234,
        cssf_detail_labels=["Specialised PSF"],
        sort_order=30,
    )
    session.commit()
    assert dto.slug == "PSF_SPECIALISED"
    assert dto.active is True
    refetched = session.query(EntityType).filter_by(slug="PSF_SPECIALISED").one()
    assert refetched.cssf_entity_filter_id == 1234


def test_deactivating_keeps_existing_applicability_rows(session):
    """Soft-delete is the key invariant: dropping a type doesn't orphan data."""
    reg = Regulation(
        reference_number="X1",
        type=RegulationType.CSSF_CIRCULAR,
        title="X",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        url="http://x",
        source_of_truth="SEED",
    )
    session.add(reg)
    session.flush()
    session.add(RegulationApplicability(
        regulation_id=reg.regulation_id,
        authorization_type="AIFM",
    ))
    session.commit()

    svc = EntityTypeService(session)
    svc.deactivate(svc.get_by_slug("AIFM").entity_type_id)
    session.commit()

    # The applicability row is untouched.
    rows = session.query(RegulationApplicability).all()
    assert len(rows) == 1
    assert rows[0].authorization_type == "AIFM"


def test_update_changes_filter_id_and_labels(session):
    svc = EntityTypeService(session)
    aifm = svc.get_by_slug("AIFM")
    svc.update(
        aifm.entity_type_id,
        label="AIFM (updated)",
        cssf_entity_filter_id=999,
        cssf_detail_labels=["NewLabel"],
        sort_order=5,
    )
    session.commit()
    refreshed = svc.get_by_slug("AIFM")
    assert refreshed.label == "AIFM (updated)"
    assert refreshed.cssf_entity_filter_id == 999
    assert refreshed.cssf_detail_labels == ["NewLabel"]
    assert refreshed.sort_order == 5


def test_reactivate(session):
    svc = EntityTypeService(session)
    aifm_id = svc.get_by_slug("AIFM").entity_type_id
    svc.deactivate(aifm_id)
    session.commit()
    assert "AIFM" not in [r.slug for r in svc.list_active()]
    svc.reactivate(aifm_id)
    session.commit()
    assert "AIFM" in [r.slug for r in svc.list_active()]
