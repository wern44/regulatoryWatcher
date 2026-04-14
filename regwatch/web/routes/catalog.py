"""Catalog list view."""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.analysis.runner import AnalysisRunner
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationOverride,
    RegulationType,
)
from regwatch.services.analysis import AnalysisService
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

        # Compute per-regulation analysis status for the column.
        analysis_svc = AnalysisService(session)
        current_by_reg: dict[int, int] = dict(
            session.query(DocumentVersion.regulation_id, DocumentVersion.version_id)
            .filter(DocumentVersion.is_current.is_(True))
            .all()
        )
        status_by_reg: dict[int, str] = {}
        for r in regs:
            latest = analysis_svc.latest_for_regulation(r.regulation_id)
            current_version_id = current_by_reg.get(r.regulation_id)
            if latest is None:
                status_by_reg[r.regulation_id] = "never"
            elif latest.status == "FAILED":
                status_by_reg[r.regulation_id] = "failed"
            elif (
                current_version_id is not None
                and latest.version_id != current_version_id
            ):
                status_by_reg[r.regulation_id] = "stale"
            else:
                status_by_reg[r.regulation_id] = "ok"

    return templates.TemplateResponse(
        request,
        "catalog/list.html",
        {
            "active": "catalog",
            "regulations": regs,
            "flt": flt,
            "status_by_reg": status_by_reg,
        },
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


@router.post("/catalog/analyse")
def catalog_analyse(
    request: Request,
    regulation_ids: Annotated[list[int], Form()],
) -> RedirectResponse:
    sf = request.app.state.session_factory
    cfg = request.app.state.config
    llm = request.app.state.llm_client
    progress = request.app.state.analysis_progress

    # Resolve selected regulations -> their current version_ids.
    with sf() as s:
        regs = (
            s.query(Regulation)
            .filter(Regulation.regulation_id.in_(regulation_ids))
            .all()
        )
        version_ids: list[int] = []
        for r in regs:
            v = next((v for v in r.versions if v.is_current), None)
            if v is not None:
                version_ids.append(v.version_id)
    if not version_ids:
        return RedirectResponse(
            "/catalog?error=no-current-versions", status_code=303
        )

    # Create the AnalysisRun row synchronously so we can redirect to its page.
    llm_model = getattr(llm, "chat_model", "") or ""
    with sf() as s:
        run = AnalysisRun(
            status=AnalysisRunStatus.RUNNING,
            queued_version_ids=version_ids,
            started_at=datetime.now(UTC),
            llm_model=llm_model,
            triggered_by="USER_UI",
        )
        s.add(run)
        s.commit()
        run_id = run.run_id

    def _progress(done: int, total: int, label: str) -> None:
        progress.tick(done, total, label)

    runner = AnalysisRunner(
        session_factory=sf,
        llm=llm,
        max_document_tokens=cfg.analysis.max_document_tokens,
        on_progress=_progress,
    )

    def _worker() -> None:
        progress.start(run_id, len(version_ids))
        try:
            runner.queue_and_run(
                version_ids,
                triggered_by="USER_UI",
                llm_model=llm_model,
                existing_run_id=run_id,
            )
            with sf() as s:
                r = s.get(AnalysisRun, run_id)
                progress.finish(r.status.value if r else "failed")
        except Exception as e:  # noqa: BLE001
            progress.finish("failed", error=str(e))
            with sf() as s:
                r = s.get(AnalysisRun, run_id)
                if r is not None:
                    r.status = AnalysisRunStatus.FAILED
                    r.finished_at = datetime.now(UTC)
                    r.error_summary = str(e)
                    s.commit()

    threading.Thread(target=_worker, daemon=True).start()
    return RedirectResponse(f"/analysis/runs/{run_id}", status_code=303)


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
