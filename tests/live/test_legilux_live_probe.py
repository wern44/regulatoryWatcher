"""Live Legilux SPARQL probe — excluded from default pytest runs.

Run manually with:
    pytest -m live tests/live/test_legilux_live_probe.py -v

Both Legilux sources failed silently in every pipeline run for months
(wrong endpoint, properties the data model doesn't have). These catch a
change of endpoint or ontology before that happens again.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from regwatch.pipeline.fetch.legilux_parliamentary import LegiluxParliamentarySource
from regwatch.pipeline.fetch.legilux_sparql import LegiluxSparqlSource

pytestmark = pytest.mark.live


def test_financial_sector_acts_are_returned() -> None:
    items = list(LegiluxSparqlSource().fetch(datetime(2020, 1, 1, tzinfo=UTC)))
    assert len(items) > 20
    assert any("12 juillet 2013" in i.title for i in items)  # AIFM law amendments


def test_finance_ministry_bills_are_returned() -> None:
    items = list(LegiluxParliamentarySource().fetch(datetime(2023, 1, 1, tzinfo=UTC)))
    assert items
    assert all(i.source_url.startswith("https://www.chd.lu/") for i in items)
