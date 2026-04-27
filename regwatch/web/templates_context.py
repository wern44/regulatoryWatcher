"""Render helper that auto-injects sidebar_badges into full-page renders.

Use `render_page` instead of `templates.TemplateResponse` for any view
that extends `base.html`. Partials and HTMX fragment endpoints should
keep using `templates.TemplateResponse` directly — they do not include
the sidebar and the extra DB hit would be wasted.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from regwatch.services.sidebar_badges import SidebarBadgeService


def render_page(
    request: Request,
    template_name: str,
    context: dict[str, Any],
) -> Any:
    """TemplateResponse with `sidebar_badges` auto-injected.

    If the caller already set `sidebar_badges` in *context*, it is
    preserved (used by tests that want to control the sidebar state).
    """
    templates = request.app.state.templates
    if "sidebar_badges" not in context:
        with request.app.state.session_factory() as session:
            badges = SidebarBadgeService(session).counts()
        context = {**context, "sidebar_badges": badges}
    return templates.TemplateResponse(request, template_name, context)
