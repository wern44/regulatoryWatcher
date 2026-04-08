"""Rule-based lifecycle classifier (runs before any Ollama backup)."""
from __future__ import annotations

import re
from datetime import date

CELEX_PROPOSAL = re.compile(r"^5\d{4}P[CP]\d{4}$")
CELEX_ADOPTED = re.compile(r"^3\d{4}[A-Z]\d{4}$")

LEGILUX_DRAFT_BILL = re.compile(
    r"data\.legilux\.public\.lu/eli/etat/projet-de-loi/",
    re.IGNORECASE,
)

CONSULTATION_KEYWORDS = (
    "consultation paper",
    "discussion paper",
    "feedback on",
    "call for evidence",
)


def classify_lifecycle(
    *,
    title: str,
    celex_id: str | None,
    url: str,
    application_date: date | None,
    today: date,
) -> str:
    """Return a lifecycle_stage string based on deterministic rules.

    Rules apply in order. The first match wins. Returns "IN_FORCE" as default.
    """
    # Rule 1: CELEX proposal prefix.
    if celex_id and CELEX_PROPOSAL.match(celex_id):
        return "PROPOSAL"

    # Rule 2: CELEX adopted + application date.
    if celex_id and CELEX_ADOPTED.match(celex_id):
        if application_date and application_date > today:
            return "ADOPTED_NOT_IN_FORCE"
        return "IN_FORCE"

    # Rule 3: Legilux draft bill URI.
    if LEGILUX_DRAFT_BILL.search(url):
        return "DRAFT_BILL"

    # Rule 4: Title heuristics for consultations.
    title_lower = title.lower()
    if any(kw in title_lower for kw in CONSULTATION_KEYWORDS):
        return "CONSULTATION"

    # Default.
    return "IN_FORCE"
