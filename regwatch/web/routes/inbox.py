"""Inbox routes: list, detail, and HTMX triage actions."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.services.inbox import SOURCE_DISPLAY_NAMES, InboxService
from regwatch.web.templates_context import render_page

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("", response_class=HTMLResponse)
def inbox_list(
    request: Request,
    source: str | None = None,
    entity_type: str | None = None,
    show_all: bool = False,
) -> HTMLResponse:
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        events = InboxService(session).list_new(
            source_display=source,
            entity_type=entity_type,
            authorization_types=auth_types,
            show_all=show_all,
        )
    source_options = sorted(set(SOURCE_DISPLAY_NAMES.values()))
    return render_page(
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


@router.post("/mark-all-seen")
def mark_all_seen(request: Request) -> RedirectResponse:
    """Mark every NEW event as SEEN, then redirect back to the inbox.

    Always operates on the full set of NEW events; UI filters are
    intentionally ignored.
    """
    with request.app.state.session_factory() as session:
        InboxService(session).mark_all_seen()
        session.commit()
    return RedirectResponse(url="/inbox", status_code=303)


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
