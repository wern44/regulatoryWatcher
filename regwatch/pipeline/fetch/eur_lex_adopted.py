"""EUR-Lex adopted acts via the CELLAR SPARQL endpoint."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from SPARQLWrapper import JSON, SPARQLWrapper

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source

ENDPOINT = "http://publications.europa.eu/webapi/rdf/sparql"


@register_source
class EurLexAdoptedSource:
    name = "eur_lex_adopted"

    def __init__(self, celex_prefixes: list[str]) -> None:
        self._celex_prefixes = celex_prefixes

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        results = self._run_query(self._build_query(since))
        now = datetime.now(UTC)
        for binding in results.get("results", {}).get("bindings", []):
            celex = binding.get("celex", {}).get("value")
            title = binding.get("title", {}).get("value", "")
            date_str = binding.get("date", {}).get("value", "")
            work_uri = binding.get("work", {}).get("value", "")
            published_at = _parse_date(date_str)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}",
                title=title,
                published_at=published_at,
                raw_payload={"celex": celex, "work_uri": work_uri, "date": date_str},
                fetched_at=now,
            )

    def _build_query(self, since: datetime) -> str:
        filter_clause = " || ".join(
            f'STR(?celex) = "{prefix}"' for prefix in self._celex_prefixes
        ) or "true"
        since_iso = since.date().isoformat()
        english = (
            "<http://publications.europa.eu/resource/authority/language/ENG>"
        )
        return f"""
        PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
        SELECT ?work ?celex ?title ?date
        WHERE {{
          ?work cdm:resource_legal_id_celex ?celex .
          ?work cdm:work_date_document ?date .
          ?expression cdm:expression_belongs_to_work ?work ;
                      cdm:expression_title ?title ;
                      cdm:expression_uses_language {english} .
          FILTER ({filter_clause})
          FILTER (?date >= "{since_iso}"^^xsd:date)
        }}
        ORDER BY DESC(?date)
        LIMIT 500
        """

    def _run_query(self, query: str) -> dict[str, Any]:
        wrapper = SPARQLWrapper(ENDPOINT)
        wrapper.setQuery(query)
        wrapper.setReturnFormat(JSON)
        return wrapper.queryAndConvert()  # type: ignore[return-value]


def _parse_date(s: str) -> datetime:
    # SPARQL returns dates as YYYY-MM-DD.
    return datetime.fromisoformat(s).replace(tzinfo=UTC)
