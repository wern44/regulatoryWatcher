"""Render helper that auto-injects sidebar_badges, entity_types, and
active_entity_type into full-page renders."""
from __future__ import annotations

from typing import Any

from fastapi import Request

from regwatch.services.entity_types import EntityTypeService
from regwatch.services.sidebar_badges import SidebarBadgeService


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
        extras["active_entity_type"] = request.cookies.get("active_entity_type", "") or ""
    final_context = {**extras, **context}
    return templates.TemplateResponse(request, template_name, final_context)
