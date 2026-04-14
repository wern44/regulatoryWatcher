from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.analysis.extractor import ExtractionResult, extract
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import Base


def _session_with_fields() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_core_fields(s)
    s.commit()
    return s


def test_extract_parses_llm_json_and_coerces_types():
    s = _session_with_fields()
    llm = MagicMock()
    llm.chat.return_value = """
    {
      "main_points": "- Point 1\\n- Point 2",
      "scope_description": "All Luxembourg AIFMs.",
      "applicable_entity_types": ["AIFM"],
      "is_ict": true,
      "ict_reasoning": "Mentions ICT risk.",
      "is_relevant_to_managed_entities": true,
      "relevance_reasoning": "AIFM applies.",
      "implementation_deadline": "2026-01-17",
      "deadline_description": "Six months after publication.",
      "document_relationship": "REPLACES",
      "relationship_target": "CSSF 12/552",
      "keywords": ["ICT", "DORA"]
    }
    """
    result = extract(
        session=s, llm=llm, regulation_metadata="CSSF 12/552 — Risk mgmt — CSSF",
        document_text="... long text ...", max_tokens=10000,
    )
    assert isinstance(result, ExtractionResult)
    assert result.status == "SUCCESS"
    assert result.values["is_ict"] is True
    assert result.values["keywords"] == ["ICT", "DORA"]
    assert result.was_truncated is False


def test_extract_flags_truncation():
    s = _session_with_fields()
    llm = MagicMock()
    llm.chat.return_value = '{"is_ict": true, "keywords": []}'
    big_text = "word " * 50000  # will exceed 10k-token budget
    result = extract(
        session=s, llm=llm, regulation_metadata="meta",
        document_text=big_text, max_tokens=1000,
    )
    assert result.was_truncated is True


def test_extract_marks_failed_on_bad_json():
    s = _session_with_fields()
    llm = MagicMock()
    llm.chat.return_value = "not json at all"
    result = extract(
        session=s, llm=llm, regulation_metadata="meta",
        document_text="doc", max_tokens=10000,
    )
    assert result.status == "FAILED"
    assert "JSON" in (result.error or "")
    assert result.raw_output == "not json at all"
