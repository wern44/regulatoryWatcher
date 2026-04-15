"""Regulation detail view route."""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from regwatch.db.models import (
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationLifecycleLink,
)
from regwatch.services.analysis import AnalysisService
from regwatch.services.cssf_discovery import CssfDiscoveryService, _slug_from_reference
from regwatch.services.regulations import build_amendment_indexes
from regwatch.services.updates import UpdateService

router = APIRouter()

_CSSF_PAGE_BASE = "https://www.cssf.lu/en/Document/"
_CSSF_REF_RE = re.compile(r"^[A-Z]+(?:-[A-Z]+)?\s+\d+/\d+$")


def _derive_cssf_page_url(reg: Regulation) -> str | None:
    """Best-effort URL to the public CSSF detail page for this regulation.

    Only for CSSF-sourced regulations. Returns None for SEED / DISCOVERED
    / EU rows where there's no CSSF detail page to derive.
    """
    if reg.source_of_truth not in ("CSSF_WEB", "CSSF_STUB"):
        return None
    ref = (reg.reference_number or "").strip()
    if not ref:
        return None
    if _CSSF_REF_RE.match(ref):
        slug = _slug_from_reference(ref)
        if slug:
            return f"{_CSSF_PAGE_BASE}{slug}/"
        return None
    # Non-CSSF publication types: ref is already a slug.
    return f"{_CSSF_PAGE_BASE}{ref}/"


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

        cssf_page_url = _derive_cssf_page_url(reg)
        stored_url = reg.url or ""
        # Separate PDF link when the stored url looks like a PDF. Avoid
        # showing it as a duplicate when the CSSF page link is present but
        # stored_url equals the page URL.
        pdf_url = stored_url if stored_url.lower().endswith(".pdf") else ""
        # Only use stored_url as a "source" link if we can't show CSSF page
        # and it isn't a bare PDF.
        source_url = "" if cssf_page_url or pdf_url else stored_url

        # Amendment grouping: children and parent info
        _effective_parent_id, children_by_parent_id = build_amendment_indexes(session)

        child_ids = children_by_parent_id.get(regulation_id, [])
        amendment_rows: list[dict] = []
        if child_ids:
            kids = session.scalars(
                select(Regulation).where(Regulation.regulation_id.in_(child_ids))
            ).all()
            kids.sort(
                key=lambda k: (k.publication_date or date.min, k.reference_number),
                reverse=True,
            )
            amendment_rows = [
                {
                    "regulation_id": k.regulation_id,
                    "reference_number": k.reference_number,
                    "title": k.title,
                    "publication_date": k.publication_date,
                    "is_ict": k.is_ict,
                }
                for k in kids
            ]

        # Parent info: direct outgoing AMENDS target (may be REPEALED)
        direct_parent_info: dict | None = None
        direct_links = session.scalars(
            select(RegulationLifecycleLink).where(
                RegulationLifecycleLink.from_regulation_id == regulation_id,
                RegulationLifecycleLink.relation == "AMENDS",
            )
        ).all()
        if direct_links:
            parent_reg = session.get(Regulation, direct_links[0].to_regulation_id)
            if parent_reg is not None:
                direct_parent_info = {
                    "regulation_id": parent_reg.regulation_id,
                    "reference_number": parent_reg.reference_number,
                    "title": parent_reg.title,
                    "is_repealed": parent_reg.lifecycle_stage == LifecycleStage.REPEALED,
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
            "cssf_page_url": cssf_page_url,
            "pdf_url": pdf_url,
            "source_url": source_url,
            "versions": versions,
            "latest_diff": latest_diff,
            "analyses_by_version": analyses_by_version,
            "amendment_rows": amendment_rows,
            "direct_parent_info": direct_parent_info,
        }

    sources_svc = CssfDiscoveryService(
        session_factory=request.app.state.session_factory,
        config=request.app.state.config.cssf_discovery,
    )
    payload["discovery_sources"] = sources_svc.list_discovery_sources(regulation_id)

    return templates.TemplateResponse(
        request, "regulation/detail.html", payload
    )
