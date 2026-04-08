"""Legilux Mémorial A SPARQL source (financial-sector laws)."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from SPARQLWrapper import JSON, SPARQLWrapper

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source

ENDPOINT = "http://data.legilux.public.lu/sparql"


@register_source
class LegiluxSparqlSource:
    name = "legilux_sparql"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        results = self._run_query(self._build_query(since))
        now = datetime.now(timezone.utc)
        for binding in results.get("results", {}).get("bindings", []):
            work_uri = binding.get("work", {}).get("value", "")
            title = binding.get("title", {}).get("value", "")
            date_str = binding.get("date", {}).get("value", "")
            eli = binding.get("eli", {}).get("value", work_uri)
            published_at = _parse_date(date_str)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=eli or work_uri,
                title=title,
                published_at=published_at,
                raw_payload={"work_uri": work_uri, "eli": eli, "date": date_str},
                fetched_at=now,
            )

    def _build_query(self, since: datetime) -> str:
        since_iso = since.date().isoformat()
        return f"""
        PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
        SELECT ?work ?title ?date ?eli WHERE {{
          ?work a jolux:Act ;
                jolux:dateDocument ?date ;
                jolux:title ?title .
          ?work jolux:eliUri ?eli .
          FILTER (?date >= "{since_iso}"^^xsd:date)
          FILTER (CONTAINS(LCASE(?title), "financ") || CONTAINS(LCASE(?title), "cssf")
                  || CONTAINS(LCASE(?title), "investissement"))
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
