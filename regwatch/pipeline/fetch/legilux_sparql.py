"""Legilux SPARQL source: Luxembourg financial-sector laws and regulations."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from SPARQLWrapper import JSON, SPARQLWrapper

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import USER_AGENT, register_source

# The Virtuoso endpoint; /sparql is the human-facing query page (HTML).
ENDPOINT = "https://data.legilux.public.lu/sparqlendpoint"

# Laws, grand-ducal / ministerial regulations and CSSF regulations whose
# Legilux subject is "place financière" (899), "fonds d'investissement" (3091)
# or "fonds d'investissement alternatif" (3444); CSSF regulations always.
# An act's URI is its ELI; its title lives on the language expression.
_QUERY = """
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX rt: <http://data.legilux.public.lu/resource/authority/resource-type/>
PREFIX ls: <http://data.legilux.public.lu/resource/authority/legal-subject/>
SELECT ?act ?date (SAMPLE(?t) AS ?title) WHERE {{
  ?act a jolux:Act ;
       jolux:typeDocument ?type ;
       jolux:dateDocument ?date ;
       jolux:isRealizedBy ?expr .
  ?expr jolux:title ?t .
  VALUES ?type {{ rt:LOI rt:RGD rt:RMIN rt:RCSF }}
  FILTER (?date >= "{since}"^^xsd:date)
  {{ ?act jolux:subjectLevel1 ?subject . VALUES ?subject {{ ls:899 ls:3091 ls:3444 }} }}
  UNION {{ ?act jolux:typeDocument rt:RCSF }}
}}
GROUP BY ?act ?date
ORDER BY DESC(?date)
LIMIT 1000
"""


@register_source
class LegiluxSparqlSource:
    name = "legilux_sparql"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        results = self._run_query(self._build_query(since))
        now = datetime.now(UTC)
        for binding in results.get("results", {}).get("bindings", []):
            eli = binding.get("act", {}).get("value", "")
            title = binding.get("title", {}).get("value", "")
            date_str = binding.get("date", {}).get("value", "")
            if not eli or not date_str:
                continue
            published_at = _parse_date(date_str)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=eli,
                # Legilux titles carry literal "\\n" sequences.
                title=" ".join(title.replace("\\n", " ").split()),
                published_at=published_at,
                raw_payload={"eli": eli, "date": date_str},
                fetched_at=now,
                # The ELI page is a JavaScript shell, identical for every act.
                document_url=f"{eli}/fr/html",
            )

    def _build_query(self, since: datetime) -> str:
        return _QUERY.format(since=since.date().isoformat())

    def _run_query(self, query: str) -> dict[str, Any]:
        wrapper = SPARQLWrapper(ENDPOINT)
        wrapper.addCustomHttpHeader("User-Agent", USER_AGENT)
        wrapper.setTimeout(60)
        wrapper.setQuery(query)
        wrapper.setReturnFormat(JSON)
        return wrapper.queryAndConvert()  # type: ignore[return-value]


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s[:10]).replace(tzinfo=UTC)
