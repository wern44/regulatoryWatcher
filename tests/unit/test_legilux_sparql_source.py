import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from regwatch.pipeline.fetch.legilux_sparql import ENDPOINT, LegiluxSparqlSource

# Trimmed response of the real endpoint (captured 2026-09-11).
FIXTURE = Path(__file__).parents[1] / "fixtures" / "legilux_sparql_sample.json"


def test_fetch_parses_financial_sector_acts() -> None:
    fixture_data = json.loads(FIXTURE.read_text())

    with patch.object(LegiluxSparqlSource, "_run_query", return_value=fixture_data):
        items = list(LegiluxSparqlSource().fetch(datetime(2000, 1, 1, tzinfo=UTC)))

    assert len(items) == 3
    assert items[0].source == "legilux_sparql"
    assert items[0].source_url.startswith("http://data.legilux.public.lu/eli/etat/leg/")
    assert items[0].raw_payload["eli"] == items[0].source_url
    assert all("\\n" not in i.title and "\n" not in i.title for i in items)
    assert items[0].title.startswith("Loi du 16 juillet 2026")


def test_query_targets_the_sparql_endpoint_and_financial_subjects() -> None:
    """The source used /sparql (an HTML page) and a query over properties
    Legilux doesn't have, so it failed in every pipeline run."""
    assert ENDPOINT == "https://data.legilux.public.lu/sparqlendpoint"
    query = LegiluxSparqlSource()._build_query(datetime(2026, 1, 2, tzinfo=UTC))
    assert '"2026-01-02"^^xsd:date' in query
    assert "jolux:isRealizedBy" in query
    assert "ls:899" in query and "rt:RCSF" in query
