"""Entity-type registry CRUD + the global 'active entity type' cookie route."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.services.entity_types import EntityTypeService
from regwatch.web.templates_context import render_page

router = APIRouter(prefix="/settings", tags=["entity_types"])

_COOKIE = "active_entity_type"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


@router.post("/active-entity-type")
def set_active_entity_type(
    request: Request,
    entity_type: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Persist the user's sidebar switcher selection in a cookie.

    Empty string = 'All entity types' (clears the cookie).
    """
    referer = request.headers.get("referer", "/")
    response = RedirectResponse(url=referer, status_code=303)
    if entity_type:
        response.set_cookie(
            _COOKIE,
            entity_type,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
    else:
        response.delete_cookie(_COOKIE)
    return response


@router.get("/entity-types", response_class=HTMLResponse)
def entity_types_list(request: Request) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        rows = EntityTypeService(session).list_all()
    return render_page(
        request,
        "settings/entity_types.html",
        {
            "active": "settings",
            "active_rows": [r for r in rows if r.active],
            "hidden_rows": [r for r in rows if not r.active],
        },
    )
