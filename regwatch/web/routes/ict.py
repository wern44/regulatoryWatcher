"""ICT / DORA route with management actions."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.db.models import Regulation, RegulationOverride
from regwatch.services.discovery import DiscoveryService
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


@router.post("/ict/{regulation_id}/unset-ict")
def unset_ict(request: Request, regulation_id: int) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        reg = session.get(Regulation, regulation_id)
        if reg:
            reg.is_ict = False
            reg.needs_review = False
            session.add(RegulationOverride(
                regulation_id=regulation_id,
                reference_number=reg.reference_number,
                action="UNSET_ICT",
                created_at=datetime.now(UTC),
            ))
            session.commit()
    return RedirectResponse(url="/ict", status_code=303)


@router.post("/ict/refresh")
def refresh_ict(request: Request) -> RedirectResponse:
    llm = request.app.state.llm_client
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        svc = DiscoveryService(session, llm=llm)
        svc.classify_catalog()
        svc.discover_missing(auth_types)
        session.commit()
    return RedirectResponse(url="/ict", status_code=303)
