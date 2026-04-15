"""Regulation detail view route."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from regwatch.db.models import DocumentVersion, Regulation
from regwatch.services.analysis import AnalysisService
from regwatch.services.cssf_discovery import CssfDiscoveryService
from regwatch.services.updates import UpdateService

router = APIRouter()


@router.get("/regulations/{regulation_id}", response_class=HTMLResponse)
def regulation_detail(request: Request, regulation_id: int) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        reg = session.get(Regulation, regulation_id)
        if reg is None:
            raise HTTPException(status_code=404)

        svc = UpdateService(session)
        versions = svc.list_versions(regulation_id)

        current = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.regulation_id == regulation_id)
            .filter(DocumentVersion.is_current.is_(True))
            .one_or_none()
        )
        latest_diff = current.change_summary if current is not None else None

        analysis_svc = AnalysisService(session)
        analyses_by_version = {
            v.version_id: analysis_svc.analyses_for_version(v.version_id)
            for v in versions
        }

        payload = {
            "active": "catalog",
            "regulation": {
                "regulation_id": reg.regulation_id,
                "reference_number": reg.reference_number,
                "title": reg.title,
                "issuing_authority": reg.issuing_authority,
                "lifecycle_stage": reg.lifecycle_stage.value,
                "is_ict": reg.is_ict,
            },
            "versions": versions,
            "latest_diff": latest_diff,
            "analyses_by_version": analyses_by_version,
        }

    sources_svc = CssfDiscoveryService(
        session_factory=request.app.state.session_factory,
        config=request.app.state.config.cssf_discovery,
    )
    payload["discovery_sources"] = sources_svc.list_discovery_sources(regulation_id)

    return templates.TemplateResponse(
        request, "regulation/detail.html", payload
    )
