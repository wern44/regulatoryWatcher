"""Database admin endpoints: export download, import upload, reset."""
from __future__ import annotations

import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from starlette.background import BackgroundTask

from regwatch.db.admin import (
    backup_database,
    reset_database,
    restore_database,
)
from regwatch.db.engine import create_app_engine

router = APIRouter(prefix="/settings/db", tags=["db-admin"])

logger = logging.getLogger(__name__)


@router.get("/export")
def export_database(request: Request) -> FileResponse:
    """Stream a fresh backup of the live database as a download."""
    config = request.app.state.config
    tmp_dir = Path(tempfile.mkdtemp(prefix="regwatch-export-"))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_file = tmp_dir / f"regwatch-backup-{stamp}.db"

    backup_database(config.paths.db_file, backup_file)

    def _cleanup() -> None:
        try:
            backup_file.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass

    return FileResponse(
        path=str(backup_file),
        filename=backup_file.name,
        media_type="application/x-sqlite3",
        background=BackgroundTask(_cleanup),
    )


@router.post("/import")
async def import_database(
    request: Request, file: UploadFile
) -> RedirectResponse:
    """Replace the current database with the uploaded backup file."""
    config = request.app.state.config

    tmp_dir = Path(tempfile.mkdtemp(prefix="regwatch-import-"))
    upload_path = tmp_dir / (file.filename or "upload.db")
    try:
        data = await file.read()
        upload_path.write_bytes(data)

        # Use a fresh engine bound to the current db_file. We deliberately
        # do NOT use app.state — restore_database will dispose this engine
        # before overwriting the file.
        engine = create_app_engine(config.paths.db_file)
        try:
            restore_database(
                engine,
                uploaded_file=upload_path,
                db_path=Path(config.paths.db_file),
            )
        except ValueError as exc:
            logger.warning("Database import rejected: %s", exc)
            return RedirectResponse(
                url=f"/settings?db_error={type(exc).__name__}",
                status_code=303,
            )
    finally:
        upload_path.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass

    return RedirectResponse(url="/settings?db_action=imported", status_code=303)


@router.post("/reset")
def reset_database_route(request: Request) -> RedirectResponse:
    """Drop every table and recreate the schema, then re-seed the catalog."""
    config = request.app.state.config

    engine = create_app_engine(config.paths.db_file)
    try:
        seed_path = Path("seeds/regulations_seed.yaml")
        reset_database(
            engine,
            embedding_dim=config.llm.embedding_dim,
            seed_file=seed_path if seed_path.exists() else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Database reset failed")
        return RedirectResponse(
            url=f"/settings?db_error={type(exc).__name__}",
            status_code=303,
        )

    return RedirectResponse(url="/settings?db_action=reset", status_code=303)
