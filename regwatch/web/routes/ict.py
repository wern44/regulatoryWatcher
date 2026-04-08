"""ICT / DORA route."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.regulations import RegulationFilter, RegulationService

router = APIRouter()


@router.get("/ict", response_class=HTMLResponse)
def ict(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(RegulationFilter(is_ict=True))
    return templates.TemplateResponse(
        request,
        "ict/list.html",
        {"active": "ict", "regulations": regs},
    )
