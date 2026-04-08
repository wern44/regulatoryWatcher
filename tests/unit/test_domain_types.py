from datetime import datetime, timezone

from regwatch.domain.types import (
    ExtractedDocument,
    MatchedDocument,
    MatchedReference,
    RawDocument,
)


def test_raw_document_dataclass() -> None:
    d = RawDocument(
        source="cssf_rss",
        source_url="https://example.com",
        title="Test",
        published_at=datetime(2026, 4, 8, tzinfo=timezone.utc),
        raw_payload={"guid": "abc"},
        fetched_at=datetime(2026, 4, 8, tzinfo=timezone.utc),
    )
    assert d.title == "Test"
    assert d.raw_payload["guid"] == "abc"


def test_extracted_document_carries_raw() -> None:
    raw = RawDocument(
        source="x",
        source_url="https://x",
        title="t",
        published_at=datetime.now(timezone.utc),
        raw_payload={},
        fetched_at=datetime.now(timezone.utc),
    )
    ext = ExtractedDocument(
        raw=raw,
        html_text="body",
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    assert ext.raw.source == "x"
    assert ext.html_text == "body"


def test_matched_document_contains_references() -> None:
    raw = RawDocument(
        source="x",
        source_url="https://x",
        title="t",
        published_at=datetime.now(timezone.utc),
        raw_payload={},
        fetched_at=datetime.now(timezone.utc),
    )
    ext = ExtractedDocument(
        raw=raw,
        html_text=None,
        pdf_path=None,
        pdf_extracted_text="text mentions CSSF 18/698",
        pdf_is_protected=False,
    )
    matched = MatchedDocument(
        extracted=ext,
        references=[
            MatchedReference(
                regulation_id=42,
                method="REGEX_ALIAS",
                confidence=1.0,
                snippet="CSSF 18/698",
            )
        ],
        lifecycle_stage="IN_FORCE",
        is_ict=False,
        severity="MATERIAL",
    )
    assert len(matched.references) == 1
    assert matched.references[0].regulation_id == 42
