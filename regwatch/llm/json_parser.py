"""Tolerant JSON parsing for LLM replies.

Local LLMs frequently wrap JSON in ```json ... ``` fences or add prose like
"Here is the JSON you requested:" before the payload. Strict `json.loads`
chokes on both. These helpers find the JSON payload inside a reply and
decode it.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _strip_fences(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped


def extract_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON OBJECT from an LLM reply that may be fenced or wrapped in prose."""
    stripped = _strip_fences(raw)
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first == -1 or last <= first:
        raise json.JSONDecodeError("no JSON object found", stripped, 0)
    return json.loads(stripped[first : last + 1])


def extract_json_array(raw: str) -> list[Any]:
    """Parse a JSON ARRAY from an LLM reply that may be fenced or wrapped in prose."""
    stripped = _strip_fences(raw)
    first = stripped.find("[")
    last = stripped.rfind("]")
    if first == -1 or last <= first:
        raise json.JSONDecodeError("no JSON array found", stripped, 0)
    return json.loads(stripped[first : last + 1])
