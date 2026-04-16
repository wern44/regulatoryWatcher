"""On-demand document fetching: download, extract text, create DocumentVersion.

Called before analysis when a regulation has no current DocumentVersion.
Downloads the PDF (or HTML) from the source URL, extracts text, creates
a version row, and removes the temporary file.  Protected PDFs are
re-rendered page-by-page via pypdfium2 before a second extraction attempt.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import trafilatura
from sqlalchemy.orm import Session

from regwatch.db.models import DocumentVersion, Regulation
from regwatch.pipeline.diff import compute_diff
from regwatch.pipeline.extract.pdf import extract_pdf_text

logger = logging.getLogger(__name__)

_USER_AGENT = "RegulatoryWatcher/1.0"
_DOWNLOAD_TIMEOUT = 60.0


class FetchError(RuntimeError):
    """Document could not be fetched or read."""


@dataclass
class FetchResult:
    version_id: int
    text_length: int
    was_rerendered: bool


def _resolve_document_url(reg: Regulation) -> str:
    """Determine the best URL to fetch the document content from.

    Priority:
      1. Stored URL if it points directly to a PDF.
      2. For CSSF-sourced regulations: scrape the detail page for the PDF link.
      3. For EU regulations with a CELEX ID: derive the EUR-Lex PDF URL.
      4. Fall back to the stored URL (may be an HTML page).

    Raises FetchError when no URL can be determined.
    """
    stored_url = (reg.url or "").strip()

    # 1. Direct PDF link already stored
    if stored_url.lower().endswith(".pdf"):
        return stored_url

    # 2. CSSF-sourced: scrape the detail page for the PDF link
    if reg.source_of_truth in ("CSSF_WEB", "CSSF_STUB") and stored_url:
        pdf_url = _resolve_cssf_pdf_url(stored_url, reg.reference_number)
        if pdf_url:
            return pdf_url

    # 3. EU regulation with CELEX ID
    if reg.celex_id:
        return (
            f"https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/"
            f"?uri=CELEX:{reg.celex_id}"
        )

    # 4. Fall back to stored URL (HTML page or whatever we have)
    if stored_url:
        return stored_url

    raise FetchError(
        f"No document URL available for {reg.reference_number}. "
        f"Upload the document manually on the detail page."
    )


def _resolve_cssf_pdf_url(detail_page_url: str, reference: str) -> str | None:
    """Fetch the CSSF detail page and extract the PDF link."""
    try:
        from regwatch.discovery.cssf_scraper import (
            CircularNotFoundError,
            fetch_circular_detail,
        )

        detail = fetch_circular_detail(detail_page_url, request_delay_ms=0)
        pdf_url = detail.pdf_url_en or detail.pdf_url_fr
        if pdf_url:
            return pdf_url
        logger.warning(
            "CSSF detail page for %s has no PDF link", reference,
        )
        return None
    except CircularNotFoundError:
        logger.warning("CSSF detail page 404 for %s: %s", reference, detail_page_url)
        return None
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to scrape CSSF detail page for %s", reference, exc_info=True,
        )
        return None


def _download(url: str) -> tuple[bytes, str]:
    """Download a URL, return (data, content_type). Raises FetchError on failure."""
    try:
        with httpx.Client(
            timeout=_DOWNLOAD_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                raise FetchError(
                    f"Document not found (HTTP 404): {url}"
                )
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            return resp.content, ct
    except FetchError:
        raise
    except httpx.HTTPStatusError as e:
        raise FetchError(
            f"HTTP {e.response.status_code} when downloading {url}"
        ) from e
    except httpx.HTTPError as e:
        raise FetchError(
            f"Network error downloading document: {e}"
        ) from e


def _is_pdf(data: bytes, content_type: str) -> bool:
    """Detect whether the downloaded content is a PDF."""
    if "pdf" in content_type.lower():
        return True
    return data[:5] == b"%PDF-"


def _rerender_protected_pdf(pdf_path: Path) -> Path:
    """Re-render a protected PDF by rasterizing pages and writing a new PDF.

    Uses pypdfium2 (already a pdfplumber dependency) to render each page
    to a PIL image, then writes a fresh, unprotected PDF from those images.
    Returns the path to the new PDF (in the same temp directory).
    """
    import pypdfium2 as pdfium
    from PIL import Image

    rerendered_path = pdf_path.with_suffix(".rerendered.pdf")
    # Try opening with no password first (works for owner-restricted PDFs),
    # then with an empty string (some producers set an empty user password).
    doc = None
    for pwd in (None, ""):
        try:
            doc = pdfium.PdfDocument(str(pdf_path), password=pwd)
            break
        except pdfium.PdfiumError:
            continue
    if doc is None:
        raise FetchError(
            "PDF is password-protected and cannot be opened. "
            "Upload the document manually."
        )
    try:
        images: list[Image.Image] = []
        for i in range(len(doc)):
            page = doc[i]
            # Render at 200 DPI — good enough for text extraction.
            bitmap = page.render(scale=200 / 72)
            pil_image = bitmap.to_pil()
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
            images.append(pil_image)

        if not images:
            raise FetchError("PDF has no pages to re-render")

        # Save all pages as a single PDF via Pillow
        images[0].save(
            str(rerendered_path), "PDF", save_all=True,
            append_images=images[1:], resolution=200,
        )
    finally:
        doc.close()

    return rerendered_path


def _extract_text_from_pdf(
    data: bytes,
) -> tuple[str, bool]:
    """Extract text from PDF bytes. Re-renders if protected.

    Returns (text, was_rerendered). Raises FetchError if all attempts fail.
    """
    with tempfile.TemporaryDirectory(prefix="regwatch_fetch_") as tmp:
        pdf_path = Path(tmp) / "document.pdf"
        pdf_path.write_bytes(data)

        text, is_protected = extract_pdf_text(pdf_path)
        if text and text.strip():
            return text.strip(), False

        if is_protected:
            logger.info("PDF is protected, attempting re-render")
            try:
                rerendered = _rerender_protected_pdf(pdf_path)
                text2, still_protected = extract_pdf_text(rerendered)
                if text2 and text2.strip():
                    return text2.strip(), True
                raise FetchError(
                    "PDF is protected and re-rendering did not produce "
                    "readable text. Upload the document manually."
                )
            except FetchError:
                raise
            except Exception as e:  # noqa: BLE001
                raise FetchError(
                    f"PDF is protected and re-rendering failed: {e}. "
                    f"Upload the document manually."
                ) from e

        raise FetchError(
            "Could not extract text from the PDF. The file may be "
            "image-based (scanned) or corrupted. Upload the document manually."
        )


def _extract_text_from_html(data: bytes, url: str) -> str:
    """Extract text from an HTML page. Raises FetchError if extraction fails."""
    html_str = data.decode("utf-8", errors="replace")
    extracted = trafilatura.extract(
        html_str, include_comments=False, include_tables=True,
    )
    if extracted and extracted.strip():
        return extracted.strip()
    raise FetchError(
        f"Could not extract text from HTML page: {url}. "
        f"Upload the document manually."
    )


def fetch_and_create_version(
    session: Session,
    regulation_id: int,
) -> FetchResult:
    """Fetch the document for a regulation and create a DocumentVersion.

    This is the main entry point. It:
      1. Resolves the best URL for the document.
      2. Downloads it.
      3. Extracts text (with protected-PDF re-rendering fallback).
      4. Creates a DocumentVersion row (idempotent by content_hash).
      5. Cleans up temporary files (no permanent archive).

    Raises FetchError with a user-facing message on failure.
    """
    reg = session.get(Regulation, regulation_id)
    if reg is None:
        raise FetchError(f"Regulation {regulation_id} not found")

    url = _resolve_document_url(reg)
    logger.info("Fetching document for %s from %s", reg.reference_number, url)

    data, content_type = _download(url)

    if _is_pdf(data, content_type):
        text, was_rerendered = _extract_text_from_pdf(data)
        html_text = None
        pdf_extracted_text = text
    else:
        text = _extract_text_from_html(data, url)
        html_text = text
        pdf_extracted_text = None
        was_rerendered = False

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    # Idempotency: if this exact content already exists, return it.
    existing = (
        session.query(DocumentVersion)
        .filter_by(regulation_id=regulation_id, content_hash=content_hash)
        .first()
    )
    if existing is not None:
        return FetchResult(
            version_id=existing.version_id,
            text_length=len(text),
            was_rerendered=was_rerendered,
        )

    # Flip the current flag on the previous version.
    current = next((v for v in reg.versions if v.is_current), None)
    prev_text = ""
    prev_number = 0
    if current is not None:
        prev_text = current.pdf_extracted_text or current.html_text or ""
        prev_number = current.version_number
        current.is_current = False
        session.flush()

    v = DocumentVersion(
        regulation_id=regulation_id,
        version_number=prev_number + 1,
        is_current=True,
        fetched_at=datetime.now(UTC),
        source_url=url,
        content_hash=content_hash,
        html_text=html_text,
        pdf_path=None,  # No permanent archive for on-demand fetches
        pdf_extracted_text=pdf_extracted_text,
        pdf_is_protected=was_rerendered,
        pdf_manual_upload=False,
        change_summary=compute_diff(prev_text, text) if prev_text else None,
    )
    session.add(v)
    session.flush()

    return FetchResult(
        version_id=v.version_id,
        text_length=len(text),
        was_rerendered=was_rerendered,
    )
