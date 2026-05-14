"""classify_entity_types() consumes an entity-type prompt string at call time."""
from __future__ import annotations

from unittest.mock import MagicMock

from regwatch.pipeline.match.classify import classify_entity_types


def test_classify_entity_types_uses_provided_prompt_segment():
    llm = MagicMock()
    llm.chat.return_value = '["AIFM"]'
    prompt = 'Valid entity_type slugs:\n- "AIFM" (AIFM)\n- "PSF_SPECIALISED" (PSF Specialised)'
    classify_entity_types(
        title="DORA outsourcing",
        text="Operational resilience...",
        llm=llm,
        entity_type_prompt=prompt,
    )
    sent_system = llm.chat.call_args.kwargs["system"]
    assert "PSF_SPECIALISED" in sent_system
    assert "AIFM" in sent_system


def test_classify_entity_types_falls_back_when_no_prompt_passed():
    """Backward compatibility: when no prompt is passed, function still runs."""
    llm = MagicMock()
    llm.chat.return_value = '["AIFM"]'
    result = classify_entity_types(title="x", text="x", llm=llm)
    assert result == ["AIFM"]
