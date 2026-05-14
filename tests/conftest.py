"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory with pdfs/ and uploads/ subdirs."""
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "uploads").mkdir()
    return tmp_path


@pytest.fixture
def seeded_entity_types(tmp_path):
    """Yield a session_factory tied to a fresh DB with the two default entity types seeded.

    Use when a test exercises CSSF discovery / classification but does NOT
    boot the full FastAPI app (which would seed automatically).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from regwatch.db.entity_type_seed import seed_default_entity_types
    from regwatch.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'fixture.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()
    return sf
