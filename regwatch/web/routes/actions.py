"""Manual actions triggered from the web UI (run pipeline now, status polling)."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.pipeline.pipeline_factory import build_runner
from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.sources import build_enabled_sources

router = APIRouter()

logger = logging.getLogger(__name__)


def _run_pipeline_in_background(
    *,
    session_factory,
    config,
    llm_client,
    progress: PipelineProgress,
) -> None:
    """Body of the worker thread. Owns its own DB session."""
    try:
        sources = build_enabled_sources(config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline source instantiation failed")
        progress.finish(run_id=None, error=f"{type(exc).__name__}: {exc}")
        return

    with session_factory() as session:
        try:
            runner = build_runner(
                session,
                sources=sources,
                archive_root=config.paths.pdf_archive,
                llm_client=llm_client,
            )
            run_id = runner.run_once(progress=progress)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("Manual pipeline run failed")
            progress.finish(run_id=None, error=f"{type(exc).__name__}: {exc}")
            return

    progress.finish(run_id=run_id)


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
        target=_run_pipeline_in_background,
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
