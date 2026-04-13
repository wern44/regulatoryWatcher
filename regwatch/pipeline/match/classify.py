"""Keyword heuristics for is_ict and severity, with optional LLM fallback."""
from __future__ import annotations

import json
import logging
from typing import Any

from regwatch.llm.client import LLMClient

logger = logging.getLogger(__name__)

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


def is_ict_document(text: str, *, llm: LLMClient | None = None) -> bool:
    lower = text.lower()
    if any(kw in lower for kw in _ICT_KEYWORDS):
        return True
    if llm is None:
        return False
    try:
        reply = llm.chat(
            system='You classify regulatory documents. Respond with ONLY "true" or "false".',
            user=(
                "Is this document related to ICT, cybersecurity, digital operational resilience, "
                "IT outsourcing, or similar technology risk topics?\n\n"
                f"Text (first 2000 chars): {text[:2000]}"
            ),
        )
        return reply.strip().lower() == "true"
    except Exception:  # noqa: BLE001
        logger.warning("LLM ICT classification unavailable, using keyword-only")
        return False


def classify_entity_types(
    title: str, text: str, *, llm: LLMClient | None = None
) -> list[str] | None:
    """Use the LLM to determine which entity types a document applies to."""
    if llm is None:
        return None
    try:
        reply = llm.chat(
            system=(
                "You analyze regulatory documents to determine which types of financial "
                "entities they apply to. Respond with ONLY a JSON array of entity type "
                'strings. Common types: "AIFM" (Alternative Investment Fund Manager), '
                '"CHAPTER15_MANCO" (UCITS Management Company), "CASP" (Crypto-Asset '
                'Service Provider), "CREDIT_INSTITUTION", "INVESTMENT_FIRM", '
                '"INSURANCE", "PENSION_FUND". If the document applies broadly to all '
                'financial entities, respond with ["ALL"].'
            ),
            user=f"Which entity types does this document apply to?\n\nTitle: {title}\nText (first 2000 chars): {text[:2000]}",
        )
        data = json.loads(reply.strip())
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:  # noqa: BLE001
        logger.warning("LLM entity type classification unavailable")
    return None


def generate_description(
    title: str, text: str, raw_payload: dict[str, Any] | None, *, llm: LLMClient | None = None
) -> str | None:
    """Extract or generate a short description for an update event."""
    if raw_payload:
        desc = raw_payload.get("description", "")
        if isinstance(desc, str) and len(desc.strip()) > 10:
            return desc.strip()[:500]

    if llm is None:
        return None
    try:
        reply = llm.chat(
            system="Summarize this regulatory document in 1-2 sentences for a compliance officer. Be concise.",
            user=f"{title}\n\n{text[:2000]}",
        )
        return reply.strip()[:500]
    except Exception:  # noqa: BLE001
        logger.warning("LLM description generation unavailable")
    return None


_AMENDMENT_MARKERS = ("amend", "amending", "repeal", "replacing", "supersede")


def severity_for(*, title: str, is_ict: bool, references_in_force: bool) -> str:
    lower = title.lower()
    is_amendment = any(marker in lower for marker in _AMENDMENT_MARKERS)
    if is_amendment and references_in_force:
        return "CRITICAL" if is_ict else "MATERIAL"
    if is_amendment or references_in_force:
        return "MATERIAL"
    return "INFORMATIONAL"
