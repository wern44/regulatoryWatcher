"""Drafts & upcoming route."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.regulations import RegulationFilter, RegulationService

router = APIRouter()


@router.get("/drafts", response_class=HTMLResponse)
def drafts(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(
            RegulationFilter(
                lifecycle_stages=[
                    "CONSULTATION",
                    "PROPOSAL",
                    "DRAFT_BILL",
                    "ADOPTED_NOT_IN_FORCE",
                ]
            )
        )
    return templates.TemplateResponse(
        request,
        "drafts/list.html",
        {"active": "drafts", "regulations": regs},
    )
