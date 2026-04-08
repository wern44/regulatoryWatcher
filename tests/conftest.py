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
