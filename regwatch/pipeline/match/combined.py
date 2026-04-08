"""Combined matcher: rules first, then Ollama-extracted references, re-resolved through rules."""
from __future__ import annotations

from sqlalchemy.orm import Session

from regwatch.domain.types import MatchedReference
from regwatch.ollama.client import OllamaClient
from regwatch.pipeline.match.ollama_refs import extract_references
from regwatch.pipeline.match.rules import RuleMatcher


class CombinedMatcher:
    def __init__(
        self, session: Session, *, ollama: OllamaClient | None = None
    ) -> None:
        self._rule_matcher = RuleMatcher(session)
        self._ollama = ollama

    def match(self, text: str) -> list[MatchedReference]:
        rule_matches = self._rule_matcher.match(text)
        if rule_matches:
            return rule_matches

        if self._ollama is None:
            return []

        extracted_refs = extract_references(self._ollama, text)
        if not extracted_refs:
            return []

        # Re-run the rule matcher on the extracted reference strings to
        # resolve them to regulation ids deterministically.
        results: list[MatchedReference] = []
        seen: set[int] = set()
        for item in extracted_refs:
            for hit in self._rule_matcher.match(item["ref"]):
                if hit.regulation_id not in seen:
                    seen.add(hit.regulation_id)
                    results.append(
                        MatchedReference(
                            regulation_id=hit.regulation_id,
                            method="OLLAMA_REFERENCE",
                            confidence=0.8,
                            snippet=item.get("context") or hit.snippet,
                        )
                    )
        return results
