"""Accept a manually-uploaded document, create a DocumentVersion, index it."""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import trafilatura
from sqlalchemy.orm import Session

from regwatch.db.models import DocumentVersion, Regulation
from regwatch.llm.client import LLMClient
from regwatch.pipeline.diff import compute_diff
from regwatch.pipeline.extract.pdf import extract_pdf_text

logger = logging.getLogger(__name__)


class UploadRejectedError(ValueError):
    """Raised when an upload is rejected before persistence."""


@dataclass
class UploadResult:
    version_id: int
    created: bool  # False if content matched an existing version
    protected: bool


_ALLOWED_EXTS = {".pdf", ".html", ".htm"}


def save_upload(
    *,
    session: Session,
    regulation_id: int,
    filename: str,
    data: bytes,
    uploads_dir: Path,
    max_size_mb: int,
) -> UploadResult:
    """Persist an uploaded file, extract its text, and create a new DocumentVersion.

    Returns UploadResult. Idempotent: if the extracted text hashes to a content_hash
    that already exists for this regulation, returns the existing version.
    """
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise UploadRejectedError(f"Unsupported file type: {ext!r}")
    if len(data) > max_size_mb * 1024 * 1024:
        raise UploadRejectedError(f"File exceeds {max_size_mb} MB")

    reg = session.get(Regulation, regulation_id)
    if reg is None:
        raise UploadRejectedError(f"No regulation with id {regulation_id}")

    # Write to disk under uploads_dir / <safe-ref> / <uuid><ext>
    safe_ref = "".join(c if c.isalnum() else "_" for c in reg.reference_number)
    dest_dir = uploads_dir / safe_ref
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(data)

    # Extract text using the existing pipeline extractors
    pdf_path: str | None = None
    pdf_text: str | None = None
    html_text: str | None = None
    protected = False

    if ext == ".pdf":
        pdf_path = str(dest)
        pdf_text, protected = extract_pdf_text(dest)
    else:
        html_str = data.decode("utf-8", errors="replace")
        extracted = trafilatura.extract(
            html_str, include_comments=False, include_tables=True
        )
        # Fall back to the raw HTML body text if trafilatura returns nothing
        # (e.g. very short fixture HTML) so we always have something to hash.
        html_text = extracted if extracted else html_str

    body = (pdf_text or html_text or "").strip()
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""

    # Idempotency: same content_hash for this regulation -> return existing version
    if content_hash:
        existing = (
            session.query(DocumentVersion)
            .filter_by(regulation_id=regulation_id, content_hash=content_hash)
            .first()
        )
        if existing is not None:
            logger.info(
                "Upload dedup: regulation=%s matched existing version %s",
                reg.reference_number,
                existing.version_id,
            )
            # Remove the new file since we won't use it
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            return UploadResult(
                version_id=existing.version_id,
                created=False,
                protected=protected,
            )

    # Flip current flag on the previous current version (if any)
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
        source_url="manual-upload",
        content_hash=content_hash,
        html_text=html_text,
        pdf_path=pdf_path,
        pdf_extracted_text=pdf_text,
        pdf_is_protected=protected,
        pdf_manual_upload=True,
        change_summary=compute_diff(prev_text, body) if prev_text else None,
    )
    session.add(v)
    session.flush()
    return UploadResult(version_id=v.version_id, created=True, protected=protected)


def index_uploaded_version(
    *,
    session: Session,
    version_id: int,
    llm: LLMClient,
    chunk_size_tokens: int,
    overlap_tokens: int,
    authorization_types: list[str],
) -> int:
    """Index a DocumentVersion's chunks. Skips protected PDFs. Returns chunk count."""
    from regwatch.rag.indexing import index_version

    v = session.get(DocumentVersion, version_id)
    if v is None or v.pdf_is_protected:
        return 0
    return index_version(
        session,
        v,
        ollama=llm,
        chunk_size_tokens=chunk_size_tokens,
        overlap_tokens=overlap_tokens,
        authorization_types=authorization_types,
    )
