"""Build the LLM prompt schema from active extraction_field rows, and coerce outputs."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from regwatch.db.models import ExtractionField, ExtractionFieldType


def build_prompt_schema(session: Session) -> str:
    """Render an active-fields schema description for the user message."""
    rows = (
        session.query(ExtractionField)
        .filter(ExtractionField.is_active == True)  # noqa: E712
        .order_by(ExtractionField.display_order, ExtractionField.name)
        .all()
    )
    lines: list[str] = []
    for f in rows:
        enum_hint = ""
        if f.data_type is ExtractionFieldType.ENUM and f.enum_values:
            enum_hint = f" (one of: {', '.join(f.enum_values)})"
        lines.append(f"- {f.name} ({f.data_type.value}{enum_hint}): {f.description}")
    return "\n".join(lines)


def coerce_value(value: Any, dtype: ExtractionFieldType) -> Any:
    """Coerce an LLM-returned raw value to the declared Python type."""
    if value is None:
        return None
    if dtype is ExtractionFieldType.BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1", "y")
        return bool(value)
    if dtype is ExtractionFieldType.DATE:
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value.strip():
            return date.fromisoformat(value.strip())
        return None
    if dtype is ExtractionFieldType.LIST_TEXT:
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [t.strip() for t in value.split(",") if t.strip()]
        return None
    if dtype is ExtractionFieldType.ENUM:
        return str(value).strip().upper()
    # TEXT, LONG_TEXT
    if isinstance(value, str):
        return value
    return str(value)
