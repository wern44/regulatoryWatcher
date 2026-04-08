import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from regwatch.pipeline.fetch.legilux_parliamentary import LegiluxParliamentarySource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "legilux_parliamentary_sample.json"


def test_fetch_parses_draft_bills() -> None:
    fixture_data = json.loads(FIXTURE.read_text())

    with patch.object(
        LegiluxParliamentarySource, "_run_query", return_value=fixture_data
    ):
        source = LegiluxParliamentarySource()
        items = list(source.fetch(datetime(2000, 1, 1, tzinfo=timezone.utc)))

    assert len(items) == 1
    assert items[0].source == "legilux_parliamentary"
    assert "projet-de-loi" in items[0].source_url
    assert items[0].raw_payload["number"] == "8628"
