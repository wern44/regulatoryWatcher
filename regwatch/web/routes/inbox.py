"""Inbox routes: list, detail, and HTMX triage actions."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.inbox import InboxService

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("", response_class=HTMLResponse)
def inbox_list(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        events = svc.list_new()
    return templates.TemplateResponse(
        request,
        "inbox/list.html",
        {"active": "inbox", "events": events},
    )


@router.post("/{event_id}/mark-seen", response_class=HTMLResponse)
def mark_seen(request: Request, event_id: int) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        svc.mark_seen(event_id)
        session.commit()
    # After marking seen the row drops out of the NEW list, so return empty.
    return HTMLResponse("")


@router.post("/{event_id}/archive", response_class=HTMLResponse)
def archive(request: Request, event_id: int) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        svc.archive(event_id)
        session.commit()
    return HTMLResponse("")
