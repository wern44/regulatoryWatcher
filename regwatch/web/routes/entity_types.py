"""Entity-type registry CRUD + the global 'active entity type' cookie route."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.discovery.cssf_scraper import (
    EntityTypeOption,
    fetch_entity_type_options,
)
from regwatch.services.entity_types import (
    EntityTypeService,
    InvalidSlugError,
    SlugConflictError,
    prompt_segment,
)
from regwatch.web.templates_context import render_page

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["entity_types"])


def _get_cssf_entity_options(request: Request) -> tuple[list[EntityTypeOption], bool]:
    """Return (options, fetched_ok). Lazy-loads and caches on app.state.

    A failed fetch caches an empty list under a flag so we don't retry on
    every page render; the user can press 'Refresh' to try again.
    """
    state = request.app.state
    cached = getattr(state, "cssf_entity_type_options", None)
    cached_ok = getattr(state, "cssf_entity_type_options_ok", None)
    if cached is not None and cached_ok is not None:
        return cached, cached_ok
    try:
        options = fetch_entity_type_options()
        state.cssf_entity_type_options = options
        state.cssf_entity_type_options_ok = True
        return options, True
    except Exception as e:  # noqa: BLE001 — scraper, network, parse all OK to swallow
        logger.warning("Could not fetch CSSF entity-type options: %s", e)
        state.cssf_entity_type_options = []
        state.cssf_entity_type_options_ok = False
        return [], False

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
    options, options_ok = _get_cssf_entity_options(request)
    return render_page(
        request,
        "settings/entity_types.html",
        {
            "active": "settings",
            "active_rows": [r for r in rows if r.active],
            "hidden_rows": [r for r in rows if not r.active],
            "cssf_entity_options": options,
            "cssf_entity_options_ok": options_ok,
        },
    )


@router.post("/entity-types/refresh-cssf-options")
def entity_types_refresh_cssf_options(request: Request) -> RedirectResponse:
    """Clear the cached CSSF entity-type options so the next render re-fetches."""
    request.app.state.cssf_entity_type_options = None
    request.app.state.cssf_entity_type_options_ok = None
    return RedirectResponse("/settings/entity-types", status_code=303)


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
