"""Manual actions triggered from the web UI (run pipeline now, status polling)."""
from __future__ import annotations

import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.run_helpers import run_pipeline_background

router = APIRouter()


@router.post("/run-pipeline", response_class=HTMLResponse)
def run_pipeline(request: Request) -> HTMLResponse:
    """Start a pipeline run in a background thread and return the progress widget.

    The widget polls `/run-pipeline/status` every 2s via HTMX. If a run is
    already in flight, we return the live widget for the existing run
    instead of starting a duplicate.
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
