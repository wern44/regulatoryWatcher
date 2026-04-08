"""Rule-based matcher: regex aliases, CELEX IDs, and ELI URIs."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from regwatch.db.models import Regulation, RegulationAlias
from regwatch.domain.types import MatchedReference

CELEX_PATTERN = re.compile(r"\b[1-9]\d{4}[A-Z]\d{4}\b")
ELI_PATTERN = re.compile(
    r"https?://data\.(?:europa\.eu|legilux\.public\.lu)/eli/[^\s)\]]+",
    re.IGNORECASE,
)


class RuleMatcher:
    def __init__(self, session: Session) -> None:
        self._session = session

    def match(self, text: str) -> list[MatchedReference]:
        if not text:
            return []

        results: list[MatchedReference] = []
        seen_keys: set[tuple[int, str]] = set()

        # 1. Regex / exact aliases.
        for alias, regulation_id in self._load_aliases():
            if alias.kind == "REGEX":
                pattern = re.compile(alias.pattern, re.IGNORECASE)
            elif alias.kind == "EXACT":
                pattern = re.compile(re.escape(alias.pattern), re.IGNORECASE)
            else:
                continue
            match = pattern.search(text)
            if match is not None:
                key = (regulation_id, "REGEX_ALIAS")
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(
                        MatchedReference(
                            regulation_id=regulation_id,
                            method="REGEX_ALIAS",
                            confidence=1.0,
                            snippet=_snippet(text, match.start(), match.end()),
                        )
                    )

        # 2. CELEX IDs.
        for celex_match in CELEX_PATTERN.finditer(text):
            celex = celex_match.group(0)
            rid = self._regulation_id_by_celex(celex)
            if rid is None:
                continue
            key = (rid, "CELEX_ID")
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(
                    MatchedReference(
                        regulation_id=rid,
                        method="CELEX_ID",
                        confidence=1.0,
                        snippet=_snippet(text, celex_match.start(), celex_match.end()),
                    )
                )

        # 3. ELI URIs.
        for eli_match in ELI_PATTERN.finditer(text):
            eli = eli_match.group(0).rstrip(".,;)")
            rid = self._regulation_id_by_eli(eli)
            if rid is None:
                continue
            key = (rid, "ELI_URI")
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(
                    MatchedReference(
                        regulation_id=rid,
                        method="ELI_URI",
                        confidence=1.0,
                        snippet=_snippet(text, eli_match.start(), eli_match.end()),
                    )
                )

        return results

    def _load_aliases(self) -> list[tuple[RegulationAlias, int]]:
        rows = (
            self._session.query(RegulationAlias, RegulationAlias.regulation_id).all()
        )
        return [(alias, rid) for alias, rid in rows]

    def _regulation_id_by_celex(self, celex: str) -> int | None:
        row = (
            self._session.query(Regulation.regulation_id)
            .filter(Regulation.celex_id == celex)
            .one_or_none()
        )
        return row[0] if row is not None else None

    def _regulation_id_by_eli(self, eli: str) -> int | None:
        row = (
            self._session.query(Regulation.regulation_id)
            .filter(Regulation.eli_uri == eli)
            .one_or_none()
        )
        return row[0] if row is not None else None


def _snippet(text: str, start: int, end: int, radius: int = 60) -> str:
    s = max(0, start - radius)
    e = min(len(text), end + radius)
    return text[s:e].strip()
