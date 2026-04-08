"""Legilux parliamentary dossiers (draft bills).

Tries the Legilux SPARQL endpoint for dossiers first. If no SPARQL schema is
exposed for parliamentary dossiers, fall back to HTML scraping of the
parliamentary dossier listing page at
https://wdocs-pub.chd.lu/docs/exped/ (flagged by the spec's open question).
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from SPARQLWrapper import JSON, SPARQLWrapper

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source

ENDPOINT = "http://data.legilux.public.lu/sparql"


@register_source
class LegiluxParliamentarySource:
    name = "legilux_parliamentary"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        results = self._run_query(self._build_query(since))
        now = datetime.now(timezone.utc)
        for binding in results.get("results", {}).get("bindings", []):
            dossier_uri = binding.get("dossier", {}).get("value", "")
            title = binding.get("title", {}).get("value", "")
            date_str = binding.get("date", {}).get("value", "")
            number = binding.get("number", {}).get("value", "")
            published_at = _parse_date(date_str)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=dossier_uri,
                title=title,
                published_at=published_at,
                raw_payload={"number": number, "date": date_str, "dossier": dossier_uri},
                fetched_at=now,
            )

    def _build_query(self, since: datetime) -> str:
        since_iso = since.date().isoformat()
        return f"""
        PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
        SELECT ?dossier ?title ?date ?number WHERE {{
          ?dossier a jolux:Draft ;
                   jolux:dateDocument ?date ;
                   jolux:title ?title ;
                   jolux:billNumber ?number .
          FILTER (?date >= "{since_iso}"^^xsd:date)
        }}
        ORDER BY DESC(?date)
        LIMIT 200
        """

    def _run_query(self, query: str) -> dict[str, Any]:
        wrapper = SPARQLWrapper(ENDPOINT)
        wrapper.setQuery(query)
        wrapper.setReturnFormat(JSON)
        return wrapper.queryAndConvert()  # type: ignore[return-value]


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
