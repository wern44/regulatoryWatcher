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


def test_query_follows_acts_amending_or_based_on_the_tracked_acts() -> None:
    """The query matched the tracked CELEX ids exactly, so it only ever
    returned the tracked acts themselves -- never their amending acts or
    the delegated / implementing acts based on them."""
    query = EurLexAdoptedSource(celex_prefixes=["32011L0061", "32022R2554"])._build_query(
        datetime(2026, 1, 2, tzinfo=timezone.utc)
    )
    assert 'VALUES ?tracked { "32011L0061" "32022R2554" }' in query
    assert "cdm:resource_legal_amends_resource_legal" in query
    assert "cdm:resource_legal_based_on_resource_legal" in query
    assert 'STRSTARTS(STR(?celex), "3")' in query
    assert '"2026-01-02"^^xsd:date' in query
