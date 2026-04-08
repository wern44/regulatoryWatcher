"""EUR-Lex proposals (CELEX prefix 5*) via the CELLAR SPARQL endpoint."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from SPARQLWrapper import JSON, SPARQLWrapper

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source

ENDPOINT = "http://publications.europa.eu/webapi/rdf/sparql"


@register_source
class EurLexProposalSource:
    name = "eur_lex_proposal"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        results = self._run_query(self._build_query(since))
        now = datetime.now(timezone.utc)
        for binding in results.get("results", {}).get("bindings", []):
            celex = binding.get("celex", {}).get("value", "")
            if not celex.startswith("5"):
                continue
            title = binding.get("title", {}).get("value", "")
            date_str = binding.get("date", {}).get("value", "")
            published_at = _parse_date(date_str)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}",
                title=title,
                published_at=published_at,
                raw_payload={"celex": celex, "date": date_str},
                fetched_at=now,
            )

    def _build_query(self, since: datetime) -> str:
        since_iso = since.date().isoformat()
        return f"""
        PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
        SELECT ?work ?celex ?title ?date
        WHERE {{
          ?work cdm:resource_legal_id_celex ?celex .
          ?work cdm:work_date_document ?date .
          ?expression cdm:expression_belongs_to_work ?work ;
                      cdm:expression_title ?title ;
                      cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> .
          FILTER (STRSTARTS(STR(?celex), "5"))
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
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
