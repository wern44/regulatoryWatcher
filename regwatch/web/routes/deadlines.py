"""Deadlines route."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.deadlines import DeadlineService

router = APIRouter()


@router.get("/deadlines", response_class=HTMLResponse)
def deadlines(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        items = svc.upcoming(window_days=730)
    return templates.TemplateResponse(
        request,
        "deadlines/list.html",
        {"active": "deadlines", "deadlines": items},
    )
