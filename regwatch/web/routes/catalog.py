"""Catalog list view."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.regulations import RegulationFilter, RegulationService

router = APIRouter()


@router.get("/catalog", response_class=HTMLResponse)
def catalog(
    request: Request,
    authorization: Literal["AIFM", "CHAPTER15_MANCO"] | None = None,
    search: str | None = None,
    lifecycle: str | None = None,
) -> HTMLResponse:
    templates = request.app.state.templates
    flt = RegulationFilter(
        authorization_type=authorization,
        search=search,
        lifecycle_stages=[lifecycle] if lifecycle else None,
    )
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(flt)

    return templates.TemplateResponse(
        request,
        "catalog/list.html",
        {"active": "catalog", "regulations": regs, "flt": flt},
    )
