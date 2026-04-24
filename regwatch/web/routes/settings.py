"""Settings view + manual PDF upload."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.db.models import DocumentVersion, ExtractionFieldType, PipelineRun
from regwatch.llm.client import HealthStatus
from regwatch.services.extraction_fields import (
    ExtractionFieldService,
    FieldNameConflictError,
    FieldNotFoundError,
    FieldProtectedError,
)
from regwatch.scheduler.jobs import FREQUENCY_OPTIONS
from regwatch.services.settings import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_class=HTMLResponse)
def settings_view(
    request: Request,
    db_action: str | None = None,
    db_error: str | None = None,
) -> HTMLResponse:
    templates = request.app.state.templates
    config = request.app.state.config
    llm = request.app.state.llm_client
    try:
        llm_health = llm.health()
    except Exception:  # noqa: BLE001
        llm_health = HealthStatus(reachable=False)
    try:
        available_models = llm.list_models()
    except Exception:  # noqa: BLE001
        available_models = []

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

    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        sched_enabled = svc.get("scheduler_enabled", "true") == "true"
        sched_freq = svc.get("scheduler_frequency", "2days")
        sched_time = svc.get("scheduler_time", "06:00")
        last_runs = (
            session.query(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .limit(2)
            .all()
        )

    scheduler_manager = getattr(request.app.state, "scheduler_manager", None)
    next_run = scheduler_manager.next_run_time() if scheduler_manager else None
    tz = ZoneInfo(config.ui.timezone)
    server_time = datetime.now(tz).strftime("%H:%M")

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "config": config,
            "llm_health": llm_health,
            "available_models": available_models,
            "current_chat_model": llm.chat_model,
            "current_embedding_model": llm.embedding_model,
            "protected_versions": protected,
            "runs": runs,
            "db_action": db_action,
            "db_error": db_error,
            "sched_enabled": sched_enabled,
            "sched_freq": sched_freq,
            "sched_time": sched_time,
            "next_run": next_run,
            "server_time": server_time,
            "server_timezone": config.ui.timezone,
            "last_runs": last_runs,
            "frequency_options": FREQUENCY_OPTIONS,
        },
    )


@router.get("/setup", response_class=HTMLResponse)
def setup_view(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    llm = request.app.state.llm_client
    try:
        models = llm.list_models()
    except Exception:  # noqa: BLE001
        models = []
    return templates.TemplateResponse(
        request,
        "settings/setup.html",
        {"models": models},
    )


@router.post("/setup")
def setup_save(
    request: Request,
    chat_model: str = Form(...),
    embedding_model: str = Form(...),
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        svc.set("chat_model", chat_model)
        svc.set("embedding_model", embedding_model)
        session.commit()
    request.app.state.llm_client.chat_model = chat_model
    request.app.state.llm_client.embedding_model = embedding_model
    return RedirectResponse(url="/", status_code=303)


@router.post("/save-models")
def save_models(
    request: Request,
    chat_model: str = Form(...),
    embedding_model: str = Form(...),
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        svc.set("chat_model", chat_model)
        svc.set("embedding_model", embedding_model)
        session.commit()
    request.app.state.llm_client.chat_model = chat_model
    request.app.state.llm_client.embedding_model = embedding_model
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/save-schedule")
def save_schedule(
    request: Request,
    scheduler_frequency: str = Form(...),
    scheduler_time: str = Form("06:00"),
    scheduler_enabled: str | None = Form(None),
) -> RedirectResponse:
    enabled = scheduler_enabled is not None
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        svc.set("scheduler_enabled", "true" if enabled else "false")
        svc.set("scheduler_frequency", scheduler_frequency)
        svc.set("scheduler_time", scheduler_time)
        session.commit()

    manager = request.app.state.scheduler_manager
    manager.apply_schedule(scheduler_frequency, scheduler_time)
    if enabled:
        manager.resume()
    else:
        manager.pause()

    return RedirectResponse(url="/settings", status_code=303)


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
        from regwatch.pipeline.extract.pdf import extract_pdf_text  # noqa: PLC0415

        text, is_protected = extract_pdf_text(target)

        version.pdf_path = str(target)
        version.pdf_extracted_text = text
        version.pdf_is_protected = is_protected
        version.pdf_manual_upload = True
        version.fetched_at = datetime.now(UTC)
        session.commit()

    return RedirectResponse(url="/settings", status_code=303)


@router.get("/extraction", response_class=HTMLResponse)
def extraction_fields_page(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        fields = ExtractionFieldService(session).list()
    return templates.TemplateResponse(
        request,
        "settings/extraction.html",
        {
            "active": "settings",
            "fields": fields,
            "data_types": list(ExtractionFieldType),
        },
    )


@router.post("/extraction")
def create_extraction_field(
    request: Request,
    name: str = Form(...),
    label: str = Form(...),
    description: str = Form(...),
    data_type: str = Form(...),
    enum_values: str = Form(""),
    display_order: int = Form(100),
) -> RedirectResponse:
    try:
        dtype = ExtractionFieldType(data_type)
    except ValueError as e:
        raise HTTPException(400, f"Invalid data_type: {data_type}") from e
    enum_list = (
        [v.strip() for v in enum_values.split(",") if v.strip()]
        if dtype is ExtractionFieldType.ENUM
        else None
    )
    with request.app.state.session_factory() as session:
        svc = ExtractionFieldService(session)
        try:
            svc.create(
                name=name,
                label=label,
                description=description,
                data_type=dtype,
                enum_values=enum_list,
                display_order=display_order,
            )
        except (FieldNameConflictError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        session.commit()
    return RedirectResponse("/settings/extraction", status_code=303)


@router.post("/extraction/{field_id}/update")
def update_extraction_field(
    request: Request,
    field_id: int,
    label: str = Form(...),
    description: str = Form(...),
    display_order: int = Form(100),
    is_active: bool = Form(False),
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        try:
            ExtractionFieldService(session).update(
                field_id,
                label=label,
                description=description,
                display_order=display_order,
                is_active=is_active,
            )
            session.commit()
        except FieldNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except FieldProtectedError as e:
            raise HTTPException(400, str(e)) from e
    return RedirectResponse("/settings/extraction", status_code=303)


@router.post("/extraction/{field_id}/delete")
def delete_extraction_field(request: Request, field_id: int) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        try:
            ExtractionFieldService(session).delete(field_id)
            session.commit()
        except FieldNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except FieldProtectedError as e:
            raise HTTPException(400, str(e)) from e
    return RedirectResponse("/settings/extraction", status_code=303)
