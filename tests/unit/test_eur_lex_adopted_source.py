import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from regwatch.pipeline.fetch.eur_lex_adopted import EurLexAdoptedSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "eur_lex_adopted_sample.json"


def test_fetch_parses_sparql_results() -> None:
    fixture_data = json.loads(FIXTURE.read_text())

    with patch.object(EurLexAdoptedSource, "_run_query", return_value=fixture_data):
        source = EurLexAdoptedSource(
            celex_prefixes=["32024L0927", "32022R2554"],
        )
        items = list(source.fetch(datetime(2000, 1, 1, tzinfo=timezone.utc)))

    assert len(items) == 2
    assert items[0].source == "eur_lex_adopted"
    assert "32024L0927" in items[0].raw_payload.get("celex", "")
    assert items[0].title.startswith("Directive")
