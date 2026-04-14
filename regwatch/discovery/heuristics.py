"""ICT classification heuristic for discovered regulations.

Lightweight, deterministic keyword match used as a fast pre-filter before
more expensive LLM-based ICT classification runs. Substring matching is
intentional — it is lenient but predictable, and the keyword list is
curated to avoid obvious false positives.
"""

from __future__ import annotations

_ICT_KEYWORDS: frozenset[str] = frozenset(
    {
        "ict",
        "information security",
        "cybersecurity",
        "cyber-security",
        "cyber security",
        "operational resilience",
        "dora",
        "outsourcing",
        "third party risk",
        "third-party risk",
        "it governance",
        "cloud",
        "business continuity",
        "nis2",
        "nis 2",
        "security risk management",
    }
)


def is_ict_by_heuristic(*, title: str, description: str) -> bool:
    """Return True if any ICT keyword appears in title or description.

    Both fields are concatenated, lowercased once, and checked via
    substring match against :data:`_ICT_KEYWORDS`. Empty strings are
    acceptable.
    """
    combined = f"{title} {description}".lower()
    for keyword in _ICT_KEYWORDS:
        if keyword in combined:
            return True
    return False
