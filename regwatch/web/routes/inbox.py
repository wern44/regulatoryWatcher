"""Inbox routes: list, detail, and HTMX triage actions."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.inbox import SOURCE_DISPLAY_NAMES, InboxService

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("", response_class=HTMLResponse)
def inbox_list(
    request: Request,
    source: str | None = None,
    entity_type: str | None = None,
    show_all: bool = False,
) -> HTMLResponse:
    templates = request.app.state.templates
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        events = svc.list_new(
            source_display=source,
            entity_type=entity_type,
            authorization_types=auth_types,
            show_all=show_all,
        )
    source_options = sorted(set(SOURCE_DISPLAY_NAMES.values()))
    return templates.TemplateResponse(
        request,
        "inbox/list.html",
        {
            "active": "inbox",
            "events": events,
            "source_options": source_options,
            "current_source": source,
            "current_entity_type": entity_type,
            "show_all": show_all,
        },
    )


@router.post("/{event_id}/mark-seen", response_class=HTMLResponse)
def mark_seen(request: Request, event_id: int) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        svc.mark_seen(event_id)
        session.commit()
    return HTMLResponse("")


@router.post("/{event_id}/archive", response_class=HTMLResponse)
def archive(request: Request, event_id: int) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        svc.archive(event_id)
        session.commit()
    return HTMLResponse("")
