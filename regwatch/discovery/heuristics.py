"""ICT classification heuristic for discovered regulations.

Lightweight, deterministic keyword match used as a fast pre-filter before
more expensive LLM-based ICT classification runs.

Single-word tokens (``ict``, ``dora``, ``cloud``, ...) use regex word
boundaries so they do not match sub-strings like ``jurisdiction`` or
``restrictive``. Multi-word phrases (``operational resilience``,
``third-party risk``) keep plain substring semantics because embedded
spaces already anchor them against partial-word collisions.
"""

from __future__ import annotations

import re

# Single-word tokens that need word-boundary matching to avoid substring
# collisions ("ict" in "jurisdiction", "dora" in "fedora", etc.).
_WORD_BOUNDARY_KEYWORDS: frozenset[str] = frozenset(
    {
        "ict",
        "dora",
        "cloud",
        "nis2",
        "cybersecurity",
        "cyber-security",
    }
)

# Multi-word phrases — substring match is safe because the spaces anchor them.
_PHRASE_KEYWORDS: frozenset[str] = frozenset(
    {
        "information security",
        "cyber security",
        "operational resilience",
        "outsourcing",
        "third party risk",
        "third-party risk",
        "it governance",
        "business continuity",
        "nis 2",
        "security risk management",
    }
)

# Combined view for introspection; every string is lowercase.
_ICT_KEYWORDS: frozenset[str] = _WORD_BOUNDARY_KEYWORDS | _PHRASE_KEYWORDS

# Compiled once: alternation of escaped keywords, bracketed by ``\b`` anchors.
_WORD_BOUNDARY_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in sorted(_WORD_BOUNDARY_KEYWORDS)) + r")\b",
    re.IGNORECASE,
)


def is_ict_by_heuristic(*, title: str, description: str) -> bool:
    """Return True if the combined title + description contains any ICT keyword.

    - Single-word tokens use regex word boundaries (``ict``, ``dora``,
      ``cloud``, ``nis2``, ...).
    - Multi-word phrases use plain substring match (``outsourcing``,
      ``operational resilience``, ...).

    Both inputs may be empty strings.
    """
    combined = f"{title} {description}".lower()
    if _WORD_BOUNDARY_RE.search(combined):
        return True
    return any(phrase in combined for phrase in _PHRASE_KEYWORDS)
