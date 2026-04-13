"""Deadlines route."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from regwatch.services.deadlines import DeadlineKind, DeadlineService

router = APIRouter()


@router.get("/deadlines", response_class=HTMLResponse)
def deadlines(
    request: Request,
    show_completed: bool = False,
) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        items = svc.upcoming(window_days=730, show_completed=show_completed)
    return templates.TemplateResponse(
        request,
        "deadlines/list.html",
        {"active": "deadlines", "deadlines": items, "show_completed": show_completed},
    )


@router.post("/deadlines/{regulation_id}/dismiss", response_class=HTMLResponse)
def dismiss_deadline(
    request: Request,
    regulation_id: int,
    kind: Annotated[DeadlineKind, Form()],
) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        svc.set_done(regulation_id, kind, done=True)
        session.commit()
    return HTMLResponse("")


@router.post("/deadlines/{regulation_id}/restore", response_class=HTMLResponse)
def restore_deadline(
    request: Request,
    regulation_id: int,
    kind: Annotated[DeadlineKind, Form()],
) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        svc.set_done(regulation_id, kind, done=False)
        session.commit()
    return HTMLResponse("")
