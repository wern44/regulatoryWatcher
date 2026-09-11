"""LLM-based extraction of regulatory references from free text."""
from __future__ import annotations

import json
import re

from regwatch.llm.client import LLMClient

_SYSTEM_PROMPT = (
    "You extract structured regulatory references from text. "
    "Output must be valid JSON and nothing else: "
    '[{"ref": "<identifier>", "context": "<surrounding phrase>"}, ...]. '
    "Identifiers include CSSF circular numbers (form: CSSF YY/NNN), "
    "EU regulation/directive numbers (form: Regulation (EU) YYYY/N, "
    "Directive (EU) YYYY/N), CELEX IDs and ELI URIs. "
    "Only list identifiers that literally occur in the text. "
    "If no references are found return []."
)

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def extract_references(client: LLMClient, text: str) -> list[dict[str, str]]:
    if not text or not text.strip():
        return []

    truncated = text[:8000]
    raw = client.chat(system=_SYSTEM_PROMPT, user=truncated)

    match = _JSON_ARRAY_RE.search(raw)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    cleaned: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict) and "ref" in item:
            cleaned.append(
                {"ref": str(item["ref"]), "context": str(item.get("context", ""))}
            )
    return cleaned
