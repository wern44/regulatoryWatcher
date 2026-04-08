from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfWriter

from regwatch.domain.types import RawDocument
from regwatch.pipeline.extract.pdf import PdfExtractionResult, extract_pdf


def _raw_with_url(url: str) -> RawDocument:
    now = datetime.now(timezone.utc)
    return RawDocument(
        source="cssf_rss",
        source_url=url,
        title="Test PDF",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )


def _make_unprotected_pdf(path: Path, text: str) -> None:
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, text)
    c.save()


def _make_protected_pdf(path: Path, tmp_path: Path) -> None:
    src = tmp_path / "src.pdf"
    _make_unprotected_pdf(src, "secret content")
    writer = PdfWriter(clone_from=str(src))
    writer.encrypt(user_password="user", owner_password="owner")
    with open(path, "wb") as f:
        writer.write(f)


def test_extract_unprotected_pdf(tmp_path: Path, httpx_mock) -> None:
    pytest.importorskip("reportlab")
    pdf_file = tmp_path / "doc.pdf"
    _make_unprotected_pdf(pdf_file, "Article 24 of AIFMD applies.")

    httpx_mock.add_response(
        url="https://example.com/doc.pdf",
        content=pdf_file.read_bytes(),
        headers={"content-type": "application/pdf"},
    )

    archive_root = tmp_path / "archive"
    result = extract_pdf(_raw_with_url("https://example.com/doc.pdf"), archive_root)

    assert isinstance(result, PdfExtractionResult)
    assert result.is_protected is False
    assert result.text is not None
    assert "Article 24" in result.text
    assert Path(result.archive_path).exists()


def test_extract_protected_pdf_sets_flag(tmp_path: Path, httpx_mock) -> None:
    pytest.importorskip("reportlab")
    src = tmp_path / "protected.pdf"
    _make_protected_pdf(src, tmp_path)

    httpx_mock.add_response(
        url="https://example.com/locked.pdf",
        content=src.read_bytes(),
        headers={"content-type": "application/pdf"},
    )

    archive_root = tmp_path / "archive"
    result = extract_pdf(_raw_with_url("https://example.com/locked.pdf"), archive_root)

    assert result.is_protected is True
    assert result.text is None
    assert Path(result.archive_path).exists()
