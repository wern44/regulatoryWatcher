"""Catalog list view."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.db.models import LifecycleStage, Regulation, RegulationOverride, RegulationType
from regwatch.services.discovery import DiscoveryService
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


@router.post("/catalog/{regulation_id}/set-ict")
def set_ict(request: Request, regulation_id: int) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        reg = session.get(Regulation, regulation_id)
        if reg:
            reg.is_ict = True
            reg.needs_review = False
            session.add(RegulationOverride(
                regulation_id=regulation_id,
                reference_number=reg.reference_number,
                action="SET_ICT",
                created_at=datetime.now(UTC),
            ))
            session.commit()
    return RedirectResponse(url="/catalog", status_code=303)


@router.post("/catalog/{regulation_id}/exclude")
def exclude_regulation(request: Request, regulation_id: int) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        reg = session.get(Regulation, regulation_id)
        if reg:
            session.add(RegulationOverride(
                regulation_id=regulation_id,
                reference_number=reg.reference_number,
                action="EXCLUDE",
                created_at=datetime.now(UTC),
            ))
            session.delete(reg)
            session.commit()
    return RedirectResponse(url="/catalog", status_code=303)


@router.post("/catalog/add")
def add_regulation(
    request: Request,
    reference_number: str = Form(...),
    title: str = Form(...),
    reg_type: str = Form("CSSF_CIRCULAR"),
    issuing_authority: str = Form("CSSF"),
    url: str = Form(""),
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        reg = Regulation(
            reference_number=reference_number,
            type=RegulationType(reg_type),
            title=title,
            issuing_authority=issuing_authority,
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            url=(
                url or "https://www.cssf.lu/en/Document/circular-"
                + reference_number.lower().replace(" ", "-")
                + "/"
            ),
            source_of_truth="MANUAL",
            needs_review=True,
        )
        session.add(reg)
        session.flush()
        session.add(RegulationOverride(
            regulation_id=reg.regulation_id,
            reference_number=reference_number,
            action="INCLUDE",
            created_at=datetime.now(UTC),
        ))
        session.commit()
    return RedirectResponse(url="/catalog", status_code=303)


@router.post("/catalog/refresh")
def refresh_catalog(request: Request) -> RedirectResponse:
    llm = request.app.state.llm_client
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        svc = DiscoveryService(session, llm=llm)
        svc.classify_catalog()
        svc.discover_missing(auth_types)
        session.commit()
    return RedirectResponse(url="/catalog", status_code=303)
