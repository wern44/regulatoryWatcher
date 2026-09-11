import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from regwatch.pipeline.fetch.eur_lex_proposal import EurLexProposalSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "eur_lex_proposal_sample.json"


def test_fetch_parses_sparql_proposals() -> None:
    fixture_data = json.loads(FIXTURE.read_text())

    with patch.object(EurLexProposalSource, "_run_query", return_value=fixture_data):
        source = EurLexProposalSource()
        items = list(source.fetch(datetime(2000, 1, 1, tzinfo=timezone.utc)))

    assert len(items) == 1
    assert items[0].source == "eur_lex_proposal"
    assert items[0].raw_payload["celex"].startswith("5")
    assert "Proposal" in items[0].title


def test_query_is_limited_to_financial_services_proposals() -> None:
    """Every Commission document (CELEX sector 5) used to match."""
    query = EurLexProposalSource()._build_query(datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert "^5[0-9]{4}PC" in query
    assert "dir-eu-legal-act/062020" in query
