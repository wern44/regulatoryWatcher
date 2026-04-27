from datetime import UTC, datetime

from regwatch.domain.types import ExtractedDocument, RawDocument
from regwatch.pipeline.hashing import content_hash, text_for_hashing


def _raw() -> RawDocument:
    now = datetime.now(UTC)
    return RawDocument(
        source="cssf_rss",
        source_url="https://example.com/x",
        title="t",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )


def test_text_for_hashing_prefers_pdf_over_html() -> None:
    extracted = ExtractedDocument(
        raw=_raw(),
        html_text="html-version",
        pdf_path=None,
        pdf_extracted_text="pdf-version",
        pdf_is_protected=False,
    )
    assert text_for_hashing(extracted) == "pdf-version"


def test_text_for_hashing_falls_back_to_html() -> None:
    extracted = ExtractedDocument(
        raw=_raw(),
        html_text="html-version",
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    assert text_for_hashing(extracted) == "html-version"


def test_text_for_hashing_strips_whitespace() -> None:
    extracted = ExtractedDocument(
        raw=_raw(),
        html_text="  hello  \n",
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    assert text_for_hashing(extracted) == "hello"


def test_text_for_hashing_returns_empty_string_when_no_text() -> None:
    extracted = ExtractedDocument(
        raw=_raw(),
        html_text=None,
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    assert text_for_hashing(extracted) == ""


def test_content_hash_is_sha256_hex() -> None:
    # SHA-256 of "abc" is a known value.
    assert content_hash("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_content_hash_is_stable() -> None:
    assert content_hash("hello world") == content_hash("hello world")
