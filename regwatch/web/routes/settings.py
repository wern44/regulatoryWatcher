"""Settings view + manual PDF upload."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.db.models import DocumentVersion, PipelineRun
from regwatch.ollama.client import HealthStatus
from regwatch.pipeline.extract.pdf import extract_pdf
from regwatch.domain.types import RawDocument

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_class=HTMLResponse)
def settings_view(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    config = request.app.state.config
    ollama = request.app.state.ollama_client
    try:
        ollama_health = ollama.health()
    except Exception:  # noqa: BLE001
        ollama_health = HealthStatus(reachable=False)

    with request.app.state.session_factory() as session:
        protected = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.pdf_is_protected.is_(True))
            .order_by(DocumentVersion.fetched_at.desc())
            .limit(20)
            .all()
        )
        runs = (
            session.query(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .limit(10)
            .all()
        )

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "config": config,
            "ollama_health": ollama_health,
            "protected_versions": protected,
            "runs": runs,
        },
    )


@router.post("/upload-pdf/{version_id}")
async def upload_pdf(
    request: Request, version_id: int, file: UploadFile
) -> RedirectResponse:
    config = request.app.state.config
    with request.app.state.session_factory() as session:
        version = session.get(DocumentVersion, version_id)
        if version is None:
            raise HTTPException(status_code=404)

        uploads_dir = Path(config.paths.uploads_dir)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        target = uploads_dir / f"v{version_id}-{file.filename}"
        data = await file.read()
        target.write_bytes(data)

        # Use the archive extractor on the uploaded file by pointing at its URL.
        # extract_pdf wants to download — for a local file we bypass the
        # HTTP path and call its helper directly.
        from regwatch.pipeline.extract.pdf import _extract_text  # noqa: PLC0415

        text, is_protected = _extract_text(target)

        version.pdf_path = str(target)
        version.pdf_extracted_text = text
        version.pdf_is_protected = is_protected
        version.pdf_manual_upload = True
        version.fetched_at = datetime.now(timezone.utc)
        session.commit()

    return RedirectResponse(url="/settings", status_code=303)


# extract_pdf/RawDocument imports are kept for backward compatibility with the
# original plan skeleton; they are unused in the in-file path above.
_ = RawDocument
_ = extract_pdf
