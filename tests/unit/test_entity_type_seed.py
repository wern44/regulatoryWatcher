"""The entity-type seeder is idempotent and matches the legacy mapping."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from regwatch.db.entity_type_seed import seed_default_entity_types
from regwatch.db.models import Base, EntityType


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        yield s


def test_seeds_two_rows_on_empty_table(session):
    inserted = seed_default_entity_types(session)
    session.commit()
    assert inserted == 2
    slugs = sorted(session.scalars(select(EntityType.slug)).all())
    assert slugs == ["AIFM", "CHAPTER15_MANCO"]


def test_aifm_carries_legacy_filter_id_and_labels(session):
    seed_default_entity_types(session)
    session.commit()
    aifm = session.scalar(select(EntityType).where(EntityType.slug == "AIFM"))
    assert aifm.cssf_entity_filter_id == 502
    assert "Alternative investment fund manager" in aifm.cssf_detail_labels
    assert "AIFM" in aifm.cssf_detail_labels


def test_chapter15_carries_legacy_filter_id_and_labels(session):
    seed_default_entity_types(session)
    session.commit()
    row = session.scalar(
        select(EntityType).where(EntityType.slug == "CHAPTER15_MANCO")
    )
    assert row.cssf_entity_filter_id == 2001
    # All five legacy substring patterns survive the migration.
    for pattern in [
        "UCITS management company",
        "UCITS management companies",
        "Chapter 15 management company",
        "Chapter 15 management companies",
        "Management company",
    ]:
        assert pattern in row.cssf_detail_labels


def test_seed_is_idempotent(session):
    first = seed_default_entity_types(session)
    session.commit()
    second = seed_default_entity_types(session)
    session.commit()
    assert first == 2
    assert second == 0
    aifm_exists = session.scalar(
        select(EntityType).where(EntityType.slug == "AIFM").exists().select()
    )
    assert aifm_exists is True


def test_seed_skips_when_any_row_already_exists(session):
    session.add(EntityType(slug="PSF_SPECIALISED", label="PSF — Specialised"))
    session.commit()
    inserted = seed_default_entity_types(session)
    session.commit()
    assert inserted == 0  # table not empty — seeder is a no-op
