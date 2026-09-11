import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from regwatch.pipeline.fetch.legilux_parliamentary import LegiluxParliamentarySource

# Trimmed response of the real endpoint (captured 2026-09-11).
FIXTURE = Path(__file__).parents[1] / "fixtures" / "legilux_parliamentary_sample.json"


def test_fetch_parses_finance_ministry_bills() -> None:
    fixture_data = json.loads(FIXTURE.read_text())

    with patch.object(
        LegiluxParliamentarySource, "_run_query", return_value=fixture_data
    ):
        items = list(
            LegiluxParliamentarySource().fetch(datetime(2000, 1, 1, tzinfo=UTC))
        )

    assert len(items) == 3
    assert items[0].source == "legilux_parliamentary"
    assert items[0].source_url == "https://www.chd.lu/fr/dossier/8806"
    assert items[0].raw_payload["number"] == "8806"
    assert items[0].title.startswith("Projet de loi")
    assert "<p" not in items[0].title


def test_query_selects_finance_ministry_drafts() -> None:
    query = LegiluxParliamentarySource()._build_query(
        datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert "jolux:InitialDraft" in query
    assert "li:MFI" in query
    assert '"2026-01-02"^^xsd:date' in query
