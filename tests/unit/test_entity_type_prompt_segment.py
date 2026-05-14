"""prompt_segment() exposes the active entity-type registry to LLM prompts."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from regwatch.db.entity_type_seed import seed_default_entity_types
from regwatch.db.models import Base, EntityType
from regwatch.services.entity_types import EntityTypeService, prompt_segment


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()
        yield s


def test_prompt_segment_lists_active_rows(session):
    out = prompt_segment(session)
    assert '"AIFM" (AIFM)' in out
    assert '"CHAPTER15_MANCO" (Chapter 15 ManCo)' in out
    assert '"ALL"' in out


def test_prompt_segment_excludes_inactive(session):
    svc = EntityTypeService(session)
    svc.deactivate(svc.get_by_slug("AIFM").entity_type_id)
    session.commit()
    out = prompt_segment(session)
    # only the AIFM slug should be gone, not the chapter15 substring
    assert "AIFM" not in out.replace("CHAPTER15", "")
    assert "CHAPTER15_MANCO" in out


def test_prompt_segment_orders_by_sort_then_slug(session):
    session.add(EntityType(slug="PSF_SUPPORT", label="PSF Support", sort_order=30))
    session.add(EntityType(slug="PSF_SPECIALISED", label="PSF Specialised", sort_order=30))
    session.commit()
    out = prompt_segment(session)
    lines = [line for line in out.splitlines() if line.startswith('- "')]
    slugs = [line.split('"')[1] for line in lines]
    assert slugs.index("AIFM") < slugs.index("CHAPTER15_MANCO")
    assert slugs.index("CHAPTER15_MANCO") < slugs.index("PSF_SPECIALISED")
    assert slugs.index("PSF_SPECIALISED") < slugs.index("PSF_SUPPORT")
    assert slugs[-1] == "ALL"
