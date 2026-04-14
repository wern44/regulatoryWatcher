"""Call the LLM with the active-fields schema and parse its JSON reply."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from regwatch.analysis.fields import build_prompt_schema, coerce_value
from regwatch.db.models import ExtractionField
from regwatch.llm.client import LLMClient
from regwatch.llm.json_parser import extract_json_object

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a regulatory-document analyst. Extract the requested fields from the "
    "document and return a JSON object with exactly the keys listed. Use null for "
    "fields not present in the document. Return ONLY the JSON object, no commentary."
)

# Conservative heuristic: 1 token ~= 4 characters for European-language text.
_CHARS_PER_TOKEN = 4


@dataclass
class ExtractionResult:
    status: str  # "SUCCESS" | "FAILED"
    raw_output: str
    was_truncated: bool
    values: dict[str, Any] = field(default_factory=dict)
    coercion_errors: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def _truncate_to_budget(text: str, max_tokens: int) -> tuple[str, bool]:
    budget = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= budget:
        return text, False
    return text[:budget], True


def extract(
    *,
    session: Session,
    llm: LLMClient,
    regulation_metadata: str,
    document_text: str,
    max_tokens: int,
) -> ExtractionResult:
    schema = build_prompt_schema(session)
    truncated_text, was_truncated = _truncate_to_budget(document_text, max_tokens)

    user_msg = (
        f"Regulation: {regulation_metadata}\n\n"
        f"Extract these fields and return valid JSON with exactly these keys:\n{schema}\n\n"
        f"--- DOCUMENT ---\n{truncated_text}\n--- END DOCUMENT ---"
    )

    try:
        raw = llm.chat(system=_SYSTEM, user=user_msg)
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM call failed: %s", e)
        return ExtractionResult(
            status="FAILED", raw_output="", was_truncated=was_truncated, error=str(e)
        )

    try:
        data = extract_json_object(raw)
    except json.JSONDecodeError as e:
        return ExtractionResult(
            status="FAILED", raw_output=raw, was_truncated=was_truncated,
            error=f"Invalid JSON in LLM reply: {e}",
        )

    fields = (
        session.query(ExtractionField)
        .filter(ExtractionField.is_active == True)  # noqa: E712
        .all()
    )
    values: dict[str, Any] = {}
    coercion_errors: dict[str, str] = {}
    for f in fields:
        raw_val = data.get(f.name)
        try:
            values[f.name] = coerce_value(raw_val, f.data_type)
        except Exception as e:  # noqa: BLE001
            logger.warning("Coercion failed for %s: %s", f.name, e)
            values[f.name] = None
            coercion_errors[f.name] = f"{type(e).__name__}: {e}"
    return ExtractionResult(
        status="SUCCESS", raw_output=raw, was_truncated=was_truncated,
        values=values, coercion_errors=coercion_errors,
    )
