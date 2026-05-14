"""Entity-type registry CRUD + the global 'active entity type' cookie route."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.services.entity_types import (
    EntityTypeService,
    InvalidSlugError,
    SlugConflictError,
    prompt_segment,
)
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


@router.post("/entity-types")
def entity_types_add(
    request: Request,
    slug: Annotated[str, Form()],
    label: Annotated[str, Form()],
    cssf_entity_filter_id: Annotated[str, Form()] = "",
    cssf_detail_labels: Annotated[str, Form()] = "",
    sort_order: Annotated[int, Form()] = 100,
) -> RedirectResponse:
    parsed_filter_id: int | None
    if cssf_entity_filter_id.strip():
        try:
            parsed_filter_id = int(cssf_entity_filter_id)
        except ValueError:
            return RedirectResponse(
                "/settings/entity-types?error=filter-id-not-int", status_code=303
            )
    else:
        parsed_filter_id = None

    parsed_labels: list[str] | None
    if cssf_detail_labels.strip():
        parsed_labels = [
            chunk.strip()
            for chunk in cssf_detail_labels.split(",")
            if chunk.strip()
        ] or None
    else:
        parsed_labels = None

    with request.app.state.session_factory() as session:
        svc = EntityTypeService(session)
        try:
            svc.create(
                slug=slug.strip(),
                label=label.strip(),
                cssf_entity_filter_id=parsed_filter_id,
                cssf_detail_labels=parsed_labels,
                sort_order=sort_order,
            )
            session.commit()
            request.app.state.entity_type_prompt = prompt_segment(session)
        except InvalidSlugError:
            return RedirectResponse(
                "/settings/entity-types?error=slug-invalid", status_code=303
            )
        except SlugConflictError:
            return RedirectResponse(
                "/settings/entity-types?error=slug-conflict", status_code=303
            )

    return RedirectResponse("/settings/entity-types", status_code=303)


@router.post("/entity-types/{entity_type_id}/deactivate")
def entity_types_deactivate(
    request: Request, entity_type_id: int
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        EntityTypeService(session).deactivate(entity_type_id)
        session.commit()
        request.app.state.entity_type_prompt = prompt_segment(session)
    return RedirectResponse("/settings/entity-types", status_code=303)


@router.post("/entity-types/{entity_type_id}/reactivate")
def entity_types_reactivate(
    request: Request, entity_type_id: int
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        EntityTypeService(session).reactivate(entity_type_id)
        session.commit()
        request.app.state.entity_type_prompt = prompt_segment(session)
    return RedirectResponse("/settings/entity-types", status_code=303)
