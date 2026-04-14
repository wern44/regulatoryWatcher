"""PDF download, archive, text extraction, and protection detection."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx
import pdfplumber
import pypdf
from slugify import slugify

from regwatch.domain.types import RawDocument


@dataclass
class PdfExtractionResult:
    archive_path: str
    text: str | None
    is_protected: bool


def extract_pdf(raw: RawDocument, archive_root: Path | str) -> PdfExtractionResult:
    """Download the PDF, archive it under `archive_root`, and extract text if possible."""
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(raw.source_url)
        response.raise_for_status()
        data = response.content

    sha = hashlib.sha256(data).hexdigest()
    when = raw.published_at
    subdir = Path(archive_root) / f"{when.year:04d}" / f"{when.month:02d}"
    subdir.mkdir(parents=True, exist_ok=True)
    slug = slugify(raw.title or "document", max_length=60)
    archive_path = subdir / f"{sha[:8]}-{slug}.pdf"
    archive_path.write_bytes(data)

    text, is_protected = extract_pdf_text(archive_path)
    return PdfExtractionResult(
        archive_path=str(archive_path), text=text, is_protected=is_protected
    )


def extract_pdf_text(pdf_path: Path) -> tuple[str | None, bool]:
    """Return (text, is_protected). text is None iff extraction failed."""
    # Pass 1: pdfplumber (most robust for layout).
    try:
        with pdfplumber.open(pdf_path) as pdf:
            parts = [page.extract_text() or "" for page in pdf.pages]
            joined = "\n".join(p for p in parts if p).strip()
            if joined:
                return joined, False
    except Exception:  # noqa: BLE001 — we fall through to pypdf
        pass

    # Pass 2: pypdf. Detect protection explicitly.
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                return None, True
            if reader.is_encrypted:
                return None, True
        parts = [(page.extract_text() or "") for page in reader.pages]
        joined = "\n".join(p for p in parts if p).strip()
        if joined:
            return joined, False
        # Empty text from an unencrypted PDF is a real failure, not protection.
        return None, False
    except pypdf.errors.PdfReadError:
        return None, True
