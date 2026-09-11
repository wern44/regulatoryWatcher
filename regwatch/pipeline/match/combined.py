"""Combined matcher: rules first, then LLM-extracted references, re-resolved through rules."""
from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy.orm import Session

from regwatch.domain.types import MatchedReference
from regwatch.llm.client import LLMClient, LLMError
from regwatch.pipeline.match.ollama_refs import extract_references
from regwatch.pipeline.match.rules import RuleMatcher

logger = logging.getLogger(__name__)


class CombinedMatcher:
    def __init__(
        self, session: Session, *, ollama: LLMClient | None = None
    ) -> None:
        self._rule_matcher = RuleMatcher(session)
        self._ollama = ollama
        # Latches to True once we've seen the LLM fail, so we stop trying for
        # the remainder of this matcher's lifetime (one pipeline run). Keeps
        # a missing model or an unreachable server from spamming per-document
        # tracebacks on every single extracted doc.
        self._ollama_disabled = False

    def match(self, text: str) -> list[MatchedReference]:
        rule_matches = self._rule_matcher.match(text)
        if rule_matches:
            return rule_matches

        if self._ollama is None or self._ollama_disabled:
            return []

        try:
            extracted_refs = extract_references(self._ollama, text)
        except (httpx.HTTPError, LLMError) as exc:
            logger.warning(
                "LLM reference extraction unavailable (%s); "
                "falling back to rule-only matching for the rest of this run.",
                exc,
            )
            self._ollama_disabled = True
            return []
        if not extracted_refs:
            return []

        # Re-run the rule matcher on the extracted reference strings to
        # resolve them to regulation ids deterministically.
        results: list[MatchedReference] = []
        seen: set[int] = set()
        for item in extracted_refs:
            if not _occurs_in(item["ref"], text):
                # Hallucinated, e.g. parroting an example from the prompt.
                continue
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


def _occurs_in(ref: str, text: str) -> bool:
    """True when the reference's numbers occur in ``text``, in order.

    "CSSF 18/698" is found in "circular 18-698"; a reference without digits
    must occur verbatim.
    """
    numbers = re.findall(r"\d+", ref)
    if not numbers:
        return ref.lower() in text.lower()
    pattern = r"(?<!\d)" + r"\D{1,3}".join(numbers) + r"(?!\d)"
    return re.search(pattern, text) is not None
