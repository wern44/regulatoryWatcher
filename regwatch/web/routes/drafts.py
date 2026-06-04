"""Drafts & upcoming route."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.regulations import RegulationFilter, RegulationService
from regwatch.services.sidebar_badges import SidebarBadgeService
from regwatch.web.templates_context import active_entity_type, render_page

router = APIRouter()


@router.get("/drafts", response_class=HTMLResponse)
def drafts(request: Request) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(
            RegulationFilter(
                authorization_type=active_entity_type(request),
                lifecycle_stages=[
                    "CONSULTATION",
                    "PROPOSAL",
                    "DRAFT_BILL",
                    "ADOPTED_NOT_IN_FORCE",
                ],
            )
        )
        previous_cutoff = SidebarBadgeService(session).mark_visited("drafts")
        session.commit()

    new_ids: set[int] = (
        {r.regulation_id for r in regs if r.created_at > previous_cutoff}
        if previous_cutoff is not None else set()
    )

    return render_page(
        request,
        "drafts/list.html",
        {
            "active": "drafts",
            "regulations": regs,
            "new_ids": new_ids,
        },
    )
