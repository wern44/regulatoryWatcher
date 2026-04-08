import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from regwatch.pipeline.fetch.legilux_sparql import LegiluxSparqlSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "legilux_sparql_sample.json"


def test_fetch_parses_memorial_a() -> None:
    fixture_data = json.loads(FIXTURE.read_text())

    with patch.object(LegiluxSparqlSource, "_run_query", return_value=fixture_data):
        source = LegiluxSparqlSource()
        items = list(source.fetch(datetime(2000, 1, 1, tzinfo=timezone.utc)))

    assert len(items) == 1
    assert items[0].source == "legilux_sparql"
    assert "AIFM" in items[0].title or "alternatifs" in items[0].title
    assert items[0].raw_payload["eli"].startswith("http://data.legilux.public.lu/eli")
