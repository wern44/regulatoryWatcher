"""Call the LLM with the active-fields schema and parse its JSON reply."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.orm import Session

from regwatch.analysis.fields import build_prompt_schema, coerce_value
from regwatch.db.models import ExtractionField
from regwatch.llm.client import LLMClient, LLMError
from regwatch.llm.json_parser import extract_json_object

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a regulatory-document analyst. Extract the requested fields from the "
    "document and return a JSON object with exactly the keys listed. Use null for "
    "fields not present in the document. Return ONLY the JSON object, no commentary."
)

# Conservative heuristic: 1 token ~= 4 characters for European-language text.
_CHARS_PER_TOKEN = 4

# Reserve tokens for the system prompt, schema, metadata, and LLM response.
_OVERHEAD_TOKENS = 600
_RESPONSE_TOKENS = 1500

# Pattern to extract n_ctx from LM Studio / llama.cpp 400 error messages.
_NCTX_RE = re.compile(r"n_ctx:\s*(\d+)")


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


def _detect_context_limit(error_body: str) -> int | None:
    """Try to extract the n_ctx value from a 400 error message."""
    m = _NCTX_RE.search(error_body)
    if m:
        return int(m.group(1))
    return None


def _build_user_msg(
    schema: str, regulation_metadata: str, document_text: str,
) -> str:
    return (
        f"Regulation: {regulation_metadata}\n\n"
        f"Extract these fields and return valid JSON with exactly these keys:\n{schema}\n\n"
        f"--- DOCUMENT ---\n{document_text}\n--- END DOCUMENT ---"
    )


def extract(
    *,
    session: Session,
    llm: LLMClient,
    regulation_metadata: str,
    document_text: str,
    max_tokens: int,
) -> ExtractionResult:
    schema = build_prompt_schema(session)

    # Calculate the overhead (schema + metadata + framing + system prompt) in tokens.
    overhead_msg = _build_user_msg(schema, regulation_metadata, "")
    prompt_overhead = len(overhead_msg) // _CHARS_PER_TOKEN + _OVERHEAD_TOKENS

    # Effective document budget: configured max_tokens caps the document portion.
    doc_budget = max(max_tokens - prompt_overhead, 500)
    truncated_text, was_truncated = _truncate_to_budget(document_text, doc_budget)
    user_msg = _build_user_msg(schema, regulation_metadata, truncated_text)

    # Try the LLM call, retrying with a smaller text on context-length errors.
    # Up to 5 attempts: first may hit a 400, subsequent may hit empty responses
    # from thinking-model reasoning tokens consuming the output budget.
    raw = ""
    last_error = ""
    for attempt in range(5):
        try:
            raw = llm.chat(system=_SYSTEM, user=user_msg)
            break
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400:
                logger.warning("LLM call failed (HTTP %s): %s", e.response.status_code, e)
                return ExtractionResult(
                    status="FAILED", raw_output="", was_truncated=was_truncated,
                    error=str(e),
                )
            # 400 likely means context overflow. Detect the limit and retry.
            body = e.response.text
            detected_ctx = _detect_context_limit(body)
            if detected_ctx:
                doc_budget = detected_ctx - prompt_overhead - _RESPONSE_TOKENS
                logger.info(
                    "Context overflow (n_ctx=%d), retrying with %d-token doc budget (attempt %d)",
                    detected_ctx, doc_budget, attempt + 2,
                )
            else:
                doc_budget = doc_budget // 2
                logger.info(
                    "HTTP 400 from LLM, halving doc budget to %d tokens (attempt %d)",
                    doc_budget, attempt + 2,
                )
            if doc_budget < 200:
                last_error = (
                    f"Document too large for model context ({detected_ctx or '?'} tokens). "
                    f"Increase the model context in LM Studio or use a shorter document."
                )
                return ExtractionResult(
                    status="FAILED", raw_output="", was_truncated=True,
                    error=last_error,
                )
            truncated_text, was_truncated = _truncate_to_budget(document_text, doc_budget)
            user_msg = _build_user_msg(schema, regulation_metadata, truncated_text)
        except LLMError as e:
            err_str = str(e)
            if "reasoning tokens" in err_str or "Empty response" in err_str:
                # Context exhausted by thinking tokens. Halve the document
                # so the model has room for both reasoning and output.
                doc_budget = doc_budget // 2
                logger.info(
                    "Empty response (context exhausted by reasoning), "
                    "halving doc budget to %d tokens (attempt %d)",
                    doc_budget, attempt + 2,
                )
                if doc_budget < 200:
                    return ExtractionResult(
                        status="FAILED", raw_output="", was_truncated=True,
                        error=(
                            "Model context too small: reasoning tokens consumed "
                            "the entire output budget. "
                            "Increase the context length in LM Studio (recommended: 32768)."
                        ),
                    )
                truncated_text, was_truncated = _truncate_to_budget(document_text, doc_budget)
                user_msg = _build_user_msg(schema, regulation_metadata, truncated_text)
                last_error = err_str
                continue
            logger.warning("LLM call failed: %s", e)
            return ExtractionResult(
                status="FAILED", raw_output="", was_truncated=was_truncated, error=err_str
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM call failed: %s", e)
            return ExtractionResult(
                status="FAILED", raw_output="", was_truncated=was_truncated, error=str(e)
            )
    else:
        return ExtractionResult(
            status="FAILED", raw_output="", was_truncated=was_truncated,
            error=last_error or "LLM call failed after retries",
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
