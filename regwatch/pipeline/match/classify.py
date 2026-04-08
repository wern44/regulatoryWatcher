"""Keyword heuristics for `is_ict` and severity."""
from __future__ import annotations

_ICT_KEYWORDS = (
    "dora",
    "ict",
    "cyber",
    "operational resilience",
    "outsourcing",
    "tlpt",
    "third-party provider",
    "third party provider",
    "incident reporting",
    "digital operational resilience",
)


def is_ict_document(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _ICT_KEYWORDS)


_AMENDMENT_MARKERS = ("amend", "amending", "repeal", "replacing", "supersede")


def severity_for(*, title: str, is_ict: bool, references_in_force: bool) -> str:
    lower = title.lower()
    is_amendment = any(marker in lower for marker in _AMENDMENT_MARKERS)
    if is_amendment and references_in_force:
        return "CRITICAL" if is_ict else "MATERIAL"
    if is_amendment or references_in_force:
        return "MATERIAL"
    return "INFORMATIONAL"
