"""Routes for the analysis run progress + result page."""
from __future__ import annotations

from fastapi import APIRouter, Request

from regwatch.services.analysis import AnalysisService

router = APIRouter()


@router.get("/analysis/runs/{run_id}")
def run_page(request: Request, run_id: int):
    with request.app.state.session_factory() as session:
        run = AnalysisService(session).get_run(run_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "analysis/run.html",
        {
            "run": run,
            "progress": request.app.state.analysis_progress,
            "run_id": run_id,
            "active": "catalog",
        },
    )


@router.get("/analysis/runs/{run_id}/status")
def run_status_fragment(request: Request, run_id: int):
    with request.app.state.session_factory() as session:
        run = AnalysisService(session).get_run(run_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "analysis/_run_status.html",
        {
            "run": run,
            "progress": request.app.state.analysis_progress,
            "run_id": run_id,
        },
    )
