"""Manual actions triggered from the web UI (run pipeline now, etc.)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from regwatch.pipeline.pipeline_factory import build_runner
from regwatch.pipeline.sources import build_enabled_sources

router = APIRouter()

logger = logging.getLogger(__name__)


@router.post("/run-pipeline")
def run_pipeline(request: Request) -> RedirectResponse:
    """Run a full pipeline pass synchronously and redirect to the dashboard.

    On success, appends `?ran=<run_id>&events=<n>` for a flash message.
    On failure, appends `?pipeline_error=<message>`.
    """
    config = request.app.state.config
    ollama = request.app.state.ollama_client

    try:
        sources = build_enabled_sources(config)
        with request.app.state.session_factory() as session:
            try:
                runner = build_runner(
                    session,
                    sources=sources,
                    archive_root=config.paths.pdf_archive,
                    ollama_client=ollama,
                )
                run_id = runner.run_once()
                session.commit()

                from regwatch.db.models import PipelineRun  # noqa: PLC0415

                run_row = session.get(PipelineRun, run_id)
                events = run_row.events_created if run_row is not None else 0
                failed = (
                    ",".join(run_row.sources_failed)
                    if run_row is not None and run_row.sources_failed
                    else ""
                )
            except Exception:
                # Make sure any partial transaction is rolled back before the
                # session (and its connection) is returned to the pool.
                session.rollback()
                raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Manual pipeline run failed")
        return RedirectResponse(
            url=f"/?pipeline_error={type(exc).__name__}",
            status_code=303,
        )

    params = f"?ran={run_id}&events={events}"
    if failed:
        params += f"&failed={failed}"
    return RedirectResponse(url=f"/{params}", status_code=303)
