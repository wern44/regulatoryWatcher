"""Tests for regwatch.services.document_fetch."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.services.document_fetch import (
    FetchError,
    _extract_text_from_pdf,
    _is_pdf,
    _resolve_document_url,
    fetch_and_create_version,
)


def _make_session(tmp_path: Path) -> tuple[Session, int]:
    """Create a fresh DB with one regulation stub, return (session, reg_id)."""
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    s = Session(engine)
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 25/900",
        title="Test Circular",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        url="https://www.cssf.lu/en/Document/circular-cssf-25-900/",
        source_of_truth="CSSF_WEB",
    )
    s.add(reg)
    s.flush()
    return s, reg.regulation_id


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


def test_resolve_url_direct_pdf():
    """Stored URL ending in .pdf is used directly."""
    reg = MagicMock(spec=Regulation)
    reg.url = "https://www.cssf.lu/wp-content/uploads/circular-25-900.pdf"
    reg.source_of_truth = "CSSF_WEB"
    reg.celex_id = None
    reg.reference_number = "CSSF 25/900"

    assert _resolve_document_url(reg) == reg.url


def test_resolve_url_cssf_scrapes_detail():
    """CSSF_WEB regulation scrapes the detail page for a PDF link."""
    reg = MagicMock(spec=Regulation)
    reg.url = "https://www.cssf.lu/en/Document/circular-cssf-25-900/"
    reg.source_of_truth = "CSSF_WEB"
    reg.celex_id = None
    reg.reference_number = "CSSF 25/900"

    with patch(
        "regwatch.services.document_fetch._resolve_cssf_pdf_url",
        return_value="https://www.cssf.lu/wp-content/uploads/doc.pdf",
    ) as mock_resolve:
        url = _resolve_document_url(reg)

    assert url == "https://www.cssf.lu/wp-content/uploads/doc.pdf"
    mock_resolve.assert_called_once_with(reg.url, reg.reference_number)


def test_resolve_url_eurlex_celex():
    """EU regulation with celex_id derives EUR-Lex PDF URL."""
    reg = MagicMock(spec=Regulation)
    reg.url = ""
    reg.source_of_truth = "SEED"
    reg.celex_id = "32022R2554"
    reg.reference_number = "DORA"

    url = _resolve_document_url(reg)
    assert "CELEX:32022R2554" in url
    assert url.endswith("?uri=CELEX:32022R2554")


def test_resolve_url_fallback_html():
    """Falls back to stored URL if nothing better is available."""
    reg = MagicMock(spec=Regulation)
    reg.url = "https://example.com/some-page"
    reg.source_of_truth = "DISCOVERED"
    reg.celex_id = None
    reg.reference_number = "TEST-001"

    assert _resolve_document_url(reg) == "https://example.com/some-page"


def test_resolve_url_no_url_raises():
    """FetchError raised when no URL can be determined."""
    reg = MagicMock(spec=Regulation)
    reg.url = ""
    reg.source_of_truth = "CSSF_STUB"
    reg.celex_id = None
    reg.reference_number = "CSSF 00/000"

    # CSSF_STUB with empty URL and no celex_id: _resolve_cssf_pdf_url
    # will be called with empty URL and return None.
    with patch(
        "regwatch.services.document_fetch._resolve_cssf_pdf_url",
        return_value=None,
    ):
        with pytest.raises(FetchError, match="No document URL"):
            _resolve_document_url(reg)


# ---------------------------------------------------------------------------
# PDF detection
# ---------------------------------------------------------------------------


def test_is_pdf_by_magic_bytes():
    assert _is_pdf(b"%PDF-1.4 ...", "application/octet-stream")


def test_is_pdf_by_content_type():
    assert _is_pdf(b"data", "application/pdf")


def test_is_not_pdf():
    assert not _is_pdf(b"<html>", "text/html")


# ---------------------------------------------------------------------------
# PDF text extraction (with re-render fallback)
# ---------------------------------------------------------------------------


def test_extract_text_from_normal_pdf(tmp_path):
    """An unprotected PDF with text content succeeds."""
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf_path = tmp_path / "test.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    c.drawString(100, 700, "Article 24 applies to AIFM entities.")
    c.save()

    text, was_rerendered = _extract_text_from_pdf(pdf_path.read_bytes())
    assert "Article 24" in text
    assert not was_rerendered


def test_extract_text_from_protected_pdf_rerenders(tmp_path):
    """When extract_pdf_text returns (None, True), re-rendering is attempted."""
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdfium2")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    # Create a normal PDF (unprotected, but we'll mock the extractor to
    # pretend it's protected so the re-render path fires).
    src = tmp_path / "source.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.drawString(100, 700, "Protected content about DORA regulation.")
    c.save()

    call_count = 0

    def _mock_extract(pdf_path):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: simulate protected PDF
            return None, True
        # Second call (after re-render): simulate successful extraction
        return "Re-rendered content about DORA regulation.", False

    with patch(
        "regwatch.services.document_fetch.extract_pdf_text",
        side_effect=_mock_extract,
    ):
        text, was_rerendered = _extract_text_from_pdf(src.read_bytes())

    assert was_rerendered
    assert call_count == 2
    assert "DORA" in text


def test_extract_text_from_user_password_pdf_raises(tmp_path):
    """A fully password-protected PDF raises FetchError."""
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdfium2")
    import pypdf
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    src = tmp_path / "source.pdf"
    c = canvas.Canvas(str(src), pagesize=A4)
    c.drawString(100, 700, "Secret content.")
    c.save()

    protected = tmp_path / "locked.pdf"
    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("real-user-password")
    with open(protected, "wb") as f:
        writer.write(f)

    with pytest.raises(FetchError, match="password-protected|re-rendering failed"):
        _extract_text_from_pdf(protected.read_bytes())


# ---------------------------------------------------------------------------
# Full fetch_and_create_version
# ---------------------------------------------------------------------------


def test_fetch_creates_version(tmp_path, httpx_mock):
    """Successful fetch creates a DocumentVersion with extracted text."""
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    # Make a test PDF
    pdf_file = tmp_path / "circular.pdf"
    c = canvas.Canvas(str(pdf_file), pagesize=A4)
    c.drawString(100, 700, "Circular about risk management.")
    c.save()
    pdf_bytes = pdf_file.read_bytes()

    httpx_mock.add_response(
        url="https://www.cssf.lu/wp-content/uploads/circ.pdf",
        content=pdf_bytes,
        headers={"content-type": "application/pdf"},
    )

    s, reg_id = _make_session(tmp_path)
    # Set the URL to the direct PDF link
    reg = s.get(Regulation, reg_id)
    reg.url = "https://www.cssf.lu/wp-content/uploads/circ.pdf"
    s.flush()

    result = fetch_and_create_version(s, reg_id)
    s.commit()

    assert result.text_length > 0
    v = s.get(DocumentVersion, result.version_id)
    assert v is not None
    assert v.is_current
    assert v.pdf_extracted_text
    assert "risk management" in v.pdf_extracted_text.lower()
    s.close()


def test_fetch_idempotent(tmp_path, httpx_mock):
    """Fetching the same content twice returns the existing version."""
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf_file = tmp_path / "circular.pdf"
    c = canvas.Canvas(str(pdf_file), pagesize=A4)
    c.drawString(100, 700, "Same content each time.")
    c.save()
    pdf_bytes = pdf_file.read_bytes()

    httpx_mock.add_response(
        url="https://www.cssf.lu/wp-content/uploads/circ.pdf",
        content=pdf_bytes,
        headers={"content-type": "application/pdf"},
    )

    s, reg_id = _make_session(tmp_path)
    reg = s.get(Regulation, reg_id)
    reg.url = "https://www.cssf.lu/wp-content/uploads/circ.pdf"
    s.flush()

    r1 = fetch_and_create_version(s, reg_id)
    s.commit()

    # Second fetch of identical content
    httpx_mock.add_response(
        url="https://www.cssf.lu/wp-content/uploads/circ.pdf",
        content=pdf_bytes,
        headers={"content-type": "application/pdf"},
    )
    r2 = fetch_and_create_version(s, reg_id)
    assert r1.version_id == r2.version_id
    s.close()


def test_fetch_html_page(tmp_path, httpx_mock):
    """Fetching an HTML page extracts text via trafilatura."""
    httpx_mock.add_response(
        url="https://example.com/regulation",
        content=b"<html><body><article>"
        b"<h1>Important Regulation</h1>"
        b"<p>This regulation applies to all financial entities.</p>"
        b"</article></body></html>",
        headers={"content-type": "text/html; charset=utf-8"},
    )

    s, reg_id = _make_session(tmp_path)
    reg = s.get(Regulation, reg_id)
    reg.url = "https://example.com/regulation"
    reg.source_of_truth = "DISCOVERED"
    s.flush()

    result = fetch_and_create_version(s, reg_id)
    s.commit()

    assert result.text_length > 0
    v = s.get(DocumentVersion, result.version_id)
    assert v is not None
    assert v.html_text
    s.close()


def test_fetch_404_raises(tmp_path, httpx_mock):
    """HTTP 404 raises FetchError with clear message."""
    httpx_mock.add_response(
        url="https://www.cssf.lu/wp-content/uploads/missing.pdf",
        status_code=404,
    )

    s, reg_id = _make_session(tmp_path)
    reg = s.get(Regulation, reg_id)
    reg.url = "https://www.cssf.lu/wp-content/uploads/missing.pdf"
    s.flush()

    with pytest.raises(FetchError, match="404"):
        fetch_and_create_version(s, reg_id)
    s.close()


def test_fetch_no_url_raises(tmp_path):
    """Regulation with no URL raises FetchError."""
    s, reg_id = _make_session(tmp_path)
    reg = s.get(Regulation, reg_id)
    reg.url = ""
    reg.source_of_truth = "SEED"
    s.flush()

    with pytest.raises(FetchError, match="No document URL"):
        fetch_and_create_version(s, reg_id)
    s.close()
