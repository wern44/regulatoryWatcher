"""Manual actions triggered from the web UI (run pipeline now, status polling)."""
from __future__ import annotations

import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.run_helpers import run_pipeline_background
from regwatch.pipeline.sources import SOURCE_GROUPS

router = APIRouter()


@router.post("/run-pipeline", response_class=HTMLResponse)
def run_pipeline(
    request: Request,
    group: str | None = Form(None),
) -> HTMLResponse:
    """Start a pipeline run in a background thread and return the progress widget.

    The widget polls `/run-pipeline/status` every 2s via HTMX. If a run is
    already in flight, we return the live widget for the existing run
    instead of starting a duplicate.

    If *group* is a valid key in :data:`SOURCE_GROUPS`, only those sources
    are executed.
    """
    progress: PipelineProgress = request.app.state.pipeline_progress
    templates = request.app.state.templates

    snapshot = progress.snapshot()
    if snapshot["status"] == "running":
        return templates.TemplateResponse(
            request,
            "partials/pipeline_progress.html",
            {"progress": snapshot},
        )

    # Block if CSSF reconciliation is writing to the DB.
    discovery_progress = getattr(request.app.state, "cssf_discovery_progress", None)
    if discovery_progress and getattr(discovery_progress, "status", None) == "running":
        progress.message = "Cannot start — CSSF reconciliation is running"
        progress.status = "failed"
        progress.error = "Wait for CSSF reconciliation to finish before running the pipeline."
        return templates.TemplateResponse(
            request,
            "partials/pipeline_progress.html",
            {"progress": progress.snapshot()},
        )

    source_names: list[str] | None = None
    if group and group in SOURCE_GROUPS:
        source_names = SOURCE_GROUPS[group]

    # Reset eagerly so the immediate response shows "running" instead of
    # whatever the previous run left behind. The background thread will call
    # reset_for_run again with the source count.
    progress.reset_for_run(total_sources=0)
    progress.message = "Initialising pipeline..."
    progress.started_at = datetime.now(UTC)

    thread = threading.Thread(
        target=run_pipeline_background,
        kwargs={
            "session_factory": request.app.state.session_factory,
            "config": request.app.state.config,
            "llm_client": request.app.state.llm_client,
            "progress": progress,
            "source_names": source_names,
        },
        name="regwatch-pipeline",
        daemon=True,
    )
    thread.start()

    return templates.TemplateResponse(
        request,
        "partials/pipeline_progress.html",
        {"progress": progress.snapshot()},
    )


@router.get("/run-pipeline/status", response_class=HTMLResponse)
def run_pipeline_status(request: Request) -> HTMLResponse:
    """HTMX polling endpoint. Returns the progress widget; self-replaces."""
    progress: PipelineProgress = request.app.state.pipeline_progress
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "partials/pipeline_progress.html",
        {"progress": progress.snapshot()},
    )


@router.post("/run-pipeline/abort", response_class=HTMLResponse)
def run_pipeline_abort(request: Request) -> HTMLResponse:
    """Request a cooperative cancel of the running pipeline.

    No-op if the pipeline is not running. The runner picks up the flag
    between documents and between sources; the in-flight document is
    allowed to finish so the DB never sees a partial write.
    """
    progress: PipelineProgress = request.app.state.pipeline_progress
    templates = request.app.state.templates

    snapshot = progress.snapshot()
    if snapshot["status"] == "running":
        progress.request_cancel()

    return templates.TemplateResponse(
        request,
        "partials/pipeline_progress.html",
        {"progress": progress.snapshot()},
    )


@router.get("/status-bar", response_class=HTMLResponse)
def status_bar(request: Request) -> HTMLResponse:
    """Global status bar fragment polled by base.html via HTMX."""
    templates = request.app.state.templates
    pipeline_progress: PipelineProgress = request.app.state.pipeline_progress
    pipeline_snap = pipeline_progress.snapshot()
    pipeline_running = pipeline_snap["status"] == "running"

    discovery_progress = getattr(
        request.app.state, "cssf_discovery_progress", None
    )
    reconciliation_running = (
        discovery_progress is not None
        and getattr(discovery_progress, "status", "idle") == "running"
    )
    recon_reference = (
        getattr(discovery_progress, "current_reference", None)
        if reconciliation_running
        else None
    )

    analysis_progress = getattr(request.app.state, "analysis_progress", None)
    analysis_running = (
        analysis_progress is not None
        and getattr(analysis_progress, "status", "idle") == "running"
    )
    analysis_label = (
        getattr(analysis_progress, "current_label", None)
        if analysis_running
        else None
    )
    analysis_cancel_requested = (
        bool(getattr(analysis_progress, "is_cancel_requested", False))
        if analysis_running
        else False
    )

    return templates.TemplateResponse(
        request,
        "partials/status_bar.html",
        {
            "pipeline_running": pipeline_running,
            "pipeline_message": pipeline_snap.get("message", ""),
            "pipeline_cancel_requested": pipeline_snap.get("cancel_requested", False),
            "reconciliation_running": reconciliation_running,
            "recon_reference": recon_reference,
            "analysis_running": analysis_running,
            "analysis_label": analysis_label,
            "analysis_cancel_requested": analysis_cancel_requested,
        },
    )


@router.post("/analysis/abort", response_class=HTMLResponse)
def analysis_abort(request: Request) -> HTMLResponse:
    """Request cooperative cancel of the running catalog refresh / analysis.

    Drives the same `analysis_progress` object that backs the analyse worker
    and the threaded /catalog/refresh worker. No-op if no analysis is running.
    """
    progress = getattr(request.app.state, "analysis_progress", None)
    if progress is not None and getattr(progress, "status", "idle") == "running":
        progress.request_cancel()

    # Re-render the status bar so the HTMX request that posted us gets the
    # updated banner with "aborting…" feedback.
    return status_bar(request)
