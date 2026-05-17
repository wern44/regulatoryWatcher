"""Render helper that auto-injects sidebar_badges, entity_types, and
active_entity_type into full-page renders."""
from __future__ import annotations

from typing import Any

from fastapi import Request

from regwatch.services.entity_types import EntityTypeService
from regwatch.services.sidebar_badges import SidebarBadgeService

ACTIVE_ENTITY_TYPE_COOKIE = "active_entity_type"


def active_entity_type(request: Request) -> str | None:
    """Return the user's sidebar entity-type selection, or None for 'All'."""
    return request.cookies.get(ACTIVE_ENTITY_TYPE_COOKIE, "") or None


def render_page(
    request: Request,
    template_name: str,
    context: dict[str, Any],
) -> Any:
    templates = request.app.state.templates
    extras: dict[str, Any] = {}
    if "sidebar_badges" not in context:
        with request.app.state.session_factory() as session:
            extras["sidebar_badges"] = SidebarBadgeService(session).counts()
    if "entity_types" not in context:
        with request.app.state.session_factory() as session:
            extras["entity_types"] = EntityTypeService(session).list_active()
    if "active_entity_type" not in context:
        extras["active_entity_type"] = active_entity_type(request) or ""
    if "active_entity_type_label" not in context:
        ets = extras.get("entity_types") or context.get("entity_types") or []
        slug = extras.get("active_entity_type") or context.get("active_entity_type") or ""
        extras["active_entity_type_label"] = next(
            (et.label for et in ets if et.slug == slug), ""
        )
    final_context = {**extras, **context}
    return templates.TemplateResponse(request, template_name, final_context)
