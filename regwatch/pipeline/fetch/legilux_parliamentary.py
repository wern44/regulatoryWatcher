"""Legilux SPARQL source: draft bills from the Ministry of Finance."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup
from SPARQLWrapper import JSON, SPARQLWrapper

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
from regwatch.pipeline.fetch.legilux_sparql import ENDPOINT

# Bills ("projets de loi") the Ministry of Finance (MFI) is in charge of,
# dated by the Government Council's approval. Titles are HTML fragments.
_QUERY = """
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX li: <http://data.legilux.public.lu/resource/authority/legal-institution/>
SELECT ?draft ?date ?number ?url (SAMPLE(?t) AS ?title) WHERE {{
  ?draft a jolux:InitialDraft ;
         jolux:institutionInChargeOfTheDraft li:MFI ;
         jolux:titleDraft ?t ;
         jolux:parliamentDraftId ?number ;
         jolux:parliamentDraftUrl ?url ;
         jolux:acceptanceCGDate ?date .
  FILTER (?date >= "{since}"^^xsd:date)
}}
GROUP BY ?draft ?date ?number ?url
ORDER BY DESC(?date)
LIMIT 500
"""


@register_source
class LegiluxParliamentarySource:
    name = "legilux_parliamentary"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        results = self._run_query(self._build_query(since))
        now = datetime.now(UTC)
        for binding in results.get("results", {}).get("bindings", []):
            url = binding.get("url", {}).get("value", "")
            date_str = binding.get("date", {}).get("value", "")
            if not url or not date_str:
                continue
            published_at = _parse_date(date_str)
            if published_at < since:
                continue
            title_html = binding.get("title", {}).get("value", "")
            number = binding.get("number", {}).get("value", "")
            yield RawDocument(
                source=self.name,
                source_url=url,
                title=BeautifulSoup(title_html, "html.parser").get_text(" ", strip=True),
                published_at=published_at,
                raw_payload={
                    "number": number,
                    "date": date_str,
                    "draft": binding.get("draft", {}).get("value", ""),
                },
                fetched_at=now,
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
