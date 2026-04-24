"""Catalog list view."""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.analysis.runner import AnalysisRunner
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    AuthorizationType,
    DiscoveryRun,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationOverride,
    RegulationType,
)
from regwatch.services.analysis import AnalysisService
from regwatch.services.cssf_discovery import CssfDiscoveryService
from regwatch.services.discovery import DiscoveryService
from regwatch.services.regulations import (
    RegulationFilter,
    RegulationService,
    build_amendment_indexes,
)
from regwatch.services.upload import (
    UploadRejectedError,
    index_uploaded_version,
    save_upload,
)

router = APIRouter()

_VALID_LIFECYCLE = {
    "IN_FORCE", "REPEALED", "AMENDED", "CONSULTATION", "PROPOSAL",
    "DRAFT_BILL", "ADOPTED_NOT_IN_FORCE",
}


_CATALOG_FILTER_COOKIE = "catalog_filters"
_CATALOG_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


@router.get("/catalog", response_class=HTMLResponse)
def catalog(
    request: Request,
    authorization: str | None = None,
    search: str | None = None,
    lifecycle: str | None = None,
    ict: str | None = None,
    show_amendments: bool = False,
    reset: bool = False,
):
    templates = request.app.state.templates

    # --- Filter persistence ---
    # If the user asked to reset, clear the cookie and show defaults.
    if reset:
        resp = RedirectResponse(url="/catalog", status_code=303)
        resp.delete_cookie(_CATALOG_FILTER_COOKIE)
        return resp

    # If the URL has no query string at all and a previous filter cookie
    # exists, restore the saved filter by redirecting.
    raw_qs = str(request.url.query or "")
    if not raw_qs:
        saved = request.cookies.get(_CATALOG_FILTER_COOKIE)
        if saved:
            return RedirectResponse(url=f"/catalog?{saved}", status_code=303)

    # Normalise authorization. The filter-bar form submits the empty string
    # ("Any authorisation") which a strict Literal type would 422 on.
    auth_value: Literal["AIFM", "CHAPTER15_MANCO"] | None
    if authorization in ("AIFM", "CHAPTER15_MANCO"):
        auth_value = authorization  # type: ignore[assignment]
    else:
        auth_value = None

    # Default to IN_FORCE; "all" means no lifecycle filter; unknown values
    # quietly fall back to the default.
    if lifecycle == "all":
        lifecycle_stages: list[str] | None = None
        effective_lifecycle = "all"
    elif lifecycle and lifecycle in _VALID_LIFECYCLE:
        lifecycle_stages = [lifecycle]
        effective_lifecycle = lifecycle
    else:
        lifecycle_stages = ["IN_FORCE"]
        effective_lifecycle = "IN_FORCE"

    # Resolve ICT
    if ict == "ict":
        is_ict_filter: bool | None = True
        effective_ict = "ict"
    elif ict == "non-ict":
        is_ict_filter = False
        effective_ict = "non-ict"
    else:
        is_ict_filter = None
        effective_ict = ""

    flt = RegulationFilter(
        authorization_type=auth_value,
        search=search,
        lifecycle_stages=lifecycle_stages,
        is_ict=is_ict_filter,
    )
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(flt)

        effective_parent_id, children_by_parent_id = build_amendment_indexes(session)

        if not show_amendments:
            # Drop any reg whose effective parent isn't itself
            regs = [
                r for r in regs
                if effective_parent_id.get(r.regulation_id) == r.regulation_id
            ]

        # Count amendments per displayed reg (flattened — chain counted as all descendants)
        amendment_counts: dict[int, int] = {}
        for r in regs:
            amendment_counts[r.regulation_id] = len(
                children_by_parent_id.get(r.regulation_id, [])
            )

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

    # Read and clear the one-shot flash cookie (set by analyse error redirects).
    flash_error = request.cookies.get("catalog_flash")
    flash_messages = {
        "no-selection": "No regulations selected for analysis.",
        "no-current-versions": "The selected regulations have no document versions yet. "
                               "Run the pipeline or upload a document first.",
    }
    flash_message = flash_messages.get(flash_error or "", "")

    # Pass effective_lifecycle, effective_ict, show_amendments to the template
    # so the dropdowns show the current selection correctly.
    response = templates.TemplateResponse(
        request,
        "catalog/list.html",
        {
            "active": "catalog",
            "regulations": regs,
            "flt": flt,
            "status_by_reg": status_by_reg,
            "effective_lifecycle": effective_lifecycle,
            "effective_ict": effective_ict,
            "show_amendments": show_amendments,
            "amendment_counts": amendment_counts,
            "flash_message": flash_message,
        },
    )
    if flash_error:
        response.delete_cookie("catalog_flash")
    # Persist the current filter query string so a bare /catalog visit
    # (e.g. coming back via a detail-page back-link) restores it.
    if raw_qs:
        response.set_cookie(
            _CATALOG_FILTER_COOKIE, raw_qs,
            max_age=_CATALOG_COOKIE_MAX_AGE,
            httponly=True, samesite="lax",
        )
    return response


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
    regulation_ids: Annotated[list[int] | None, Form()] = None,
    version_ids: Annotated[list[int] | None, Form()] = None,
) -> RedirectResponse:
    sf = request.app.state.session_factory
    cfg = request.app.state.config
    llm = request.app.state.llm_client
    progress = request.app.state.analysis_progress

    regulation_ids = regulation_ids or []
    version_ids = version_ids or []

    if not regulation_ids and not version_ids:
        resp = RedirectResponse("/catalog", status_code=303)
        resp.set_cookie(
            "catalog_flash", "no-selection",
            max_age=10, httponly=True, samesite="lax",
        )
        return resp

    # Resolve regulation_ids -> current versions, noting which need fetching.
    resolved_version_ids: list[int] = list(version_ids) if version_ids else []
    needs_fetch_ids: list[int] = []

    if regulation_ids and not version_ids:
        with sf() as s:
            regs = (
                s.query(Regulation)
                .filter(Regulation.regulation_id.in_(regulation_ids))
                .all()
            )
            for r in regs:
                v = next((v for v in r.versions if v.is_current), None)
                if v is not None:
                    resolved_version_ids.append(v.version_id)
                else:
                    needs_fetch_ids.append(r.regulation_id)

    if not resolved_version_ids and not needs_fetch_ids:
        resp = RedirectResponse("/catalog", status_code=303)
        resp.set_cookie(
            "catalog_flash", "no-selection",
            max_age=10, httponly=True, samesite="lax",
        )
        return resp

    # Estimate total work items: existing versions + regulations to fetch.
    total_items = len(resolved_version_ids) + len(needs_fetch_ids)

    # Create the AnalysisRun row synchronously so we can redirect to its page.
    llm_model = getattr(llm, "chat_model", "") or ""
    with sf() as s:
        run = AnalysisRun(
            status=AnalysisRunStatus.RUNNING,
            queued_version_ids=resolved_version_ids,
            started_at=datetime.now(UTC),
            llm_model=llm_model,
            triggered_by="USER_UI",
        )
        s.add(run)
        s.commit()
        run_id = run.run_id

    def _worker() -> None:
        from regwatch.services.document_fetch import FetchError, fetch_and_create_version

        progress.start(run_id, total_items)
        fetch_errors: list[str] = []
        all_version_ids = list(resolved_version_ids)

        # Phase 1: fetch missing documents
        for i, reg_id in enumerate(needs_fetch_ids, start=1):
            with sf() as s:
                reg = s.get(Regulation, reg_id)
                label = reg.reference_number if reg else f"regulation {reg_id}"
            progress.tick(i, total_items, f"Fetching: {label}")

            try:
                with sf() as s:
                    result = fetch_and_create_version(s, reg_id)
                    s.commit()
                all_version_ids.append(result.version_id)
            except FetchError as e:
                fetch_errors.append(f"{label}: {e}")
            except Exception as e:  # noqa: BLE001
                fetch_errors.append(f"{label}: unexpected error — {e}")

        # Update the run's queued_version_ids now that we know the full set.
        with sf() as s:
            r = s.get(AnalysisRun, run_id)
            if r is not None:
                r.queued_version_ids = all_version_ids
                s.commit()

        if not all_version_ids:
            # Nothing to analyse — all fetches failed.
            with sf() as s:
                r = s.get(AnalysisRun, run_id)
                if r is not None:
                    r.status = AnalysisRunStatus.FAILED
                    r.finished_at = datetime.now(UTC)
                    r.error_summary = "\n".join(fetch_errors)
                    s.commit()
            progress.finish("FAILED", error="All document fetches failed")
            return

        # Phase 2: analyse
        new_total = len(needs_fetch_ids) + len(all_version_ids)
        progress.tick(
            len(needs_fetch_ids), new_total, "Starting analysis\u2026",
        )
        try:
            runner = AnalysisRunner(
                session_factory=sf,
                llm=llm,
                max_document_tokens=cfg.analysis.max_document_tokens,
                on_progress=lambda done, total, label: progress.tick(
                    len(needs_fetch_ids) + done, new_total, label,
                ),
            )
            runner.queue_and_run(
                all_version_ids,
                triggered_by="USER_UI",
                llm_model=llm_model,
                existing_run_id=run_id,
            )
            # Append fetch errors to the run summary if any.
            if fetch_errors:
                with sf() as s:
                    r = s.get(AnalysisRun, run_id)
                    if r is not None:
                        existing = r.error_summary or ""
                        fetch_block = "Fetch errors:\n" + "\n".join(fetch_errors)
                        r.error_summary = (
                            f"{existing}\n{fetch_block}" if existing
                            else fetch_block
                        )
                        if r.status == AnalysisRunStatus.SUCCESS:
                            r.status = AnalysisRunStatus.PARTIAL
                        s.commit()
            with sf() as s:
                r = s.get(AnalysisRun, run_id)
                progress.finish(r.status.value if r else "FAILED")
        except Exception as e:  # noqa: BLE001
            progress.finish("FAILED", error=str(e))
            with sf() as s:
                r = s.get(AnalysisRun, run_id)
                if r is not None:
                    r.status = AnalysisRunStatus.FAILED
                    r.finished_at = datetime.now(UTC)
                    r.error_summary = str(e)
                    s.commit()

    threading.Thread(target=_worker, daemon=True).start()
    return RedirectResponse(f"/analysis/runs/{run_id}", status_code=303)


@router.post("/catalog/{regulation_id}/upload")
async def upload_document(
    request: Request,
    regulation_id: int,
    file: UploadFile,
) -> RedirectResponse:
    cfg = request.app.state.config
    sf = request.app.state.session_factory
    data = await file.read()

    uploads_dir_str = getattr(cfg.paths, "uploads_dir", None) or cfg.paths.pdf_archive
    uploads_dir = Path(uploads_dir_str)

    try:
        with sf() as s:
            result = save_upload(
                session=s,
                regulation_id=regulation_id,
                filename=file.filename or "upload",
                data=data,
                uploads_dir=uploads_dir,
                max_size_mb=cfg.analysis.max_upload_size_mb,
            )
            s.commit()

            if result.created:
                auth_types = [a.type for a in cfg.entity.authorizations]
                index_uploaded_version(
                    session=s,
                    version_id=result.version_id,
                    llm=request.app.state.llm_client,
                    chunk_size_tokens=cfg.rag.chunk_size_tokens,
                    overlap_tokens=cfg.rag.chunk_overlap_tokens,
                    authorization_types=auth_types,
                )
                s.commit()
    except UploadRejectedError as e:
        return RedirectResponse(
            f"/regulations/{regulation_id}?error={e}",
            status_code=303,
        )

    return RedirectResponse(
        f"/regulations/{regulation_id}?uploaded=1&version_id={result.version_id}",
        status_code=303,
    )


@router.post("/catalog/discover-cssf")
def catalog_discover_cssf(
    request: Request,
    mode: Annotated[str, Form()] = "incremental",
    entity_types: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    """Queue a CSSF discovery run and redirect to its progress page."""
    sf = request.app.state.session_factory
    cfg = request.app.state.config
    progress = request.app.state.cssf_discovery_progress

    # Prevent concurrent DB writes with the pipeline.
    pipeline_progress = request.app.state.pipeline_progress
    if pipeline_progress.snapshot()["status"] == "running":
        return RedirectResponse(
            "/settings?db_error=Cannot+start+reconciliation+while+pipeline+is+running",
            status_code=303,
        )

    if entity_types:
        auth_types: list[AuthorizationType] = []
        for name in entity_types:
            try:
                auth_types.append(AuthorizationType(name))
            except ValueError:
                pass
    else:
        auth_types = [AuthorizationType(a.type) for a in cfg.entity.authorizations]

    if not auth_types:
        return RedirectResponse("/catalog?error=no-entity-types", status_code=303)

    if mode not in ("incremental", "full"):
        mode = "incremental"

    # Create the DiscoveryRun row synchronously so the redirect target exists.
    with sf() as s:
        run = DiscoveryRun(
            status="RUNNING",
            started_at=datetime.now(UTC),
            triggered_by="USER_UI",
            entity_types=[et.value for et in auth_types],
            mode=mode,
        )
        s.add(run)
        s.commit()
        run_id = run.run_id

    def _progress(**kw: object) -> None:
        progress.tick(
            **{
                k: v
                for k, v in kw.items()
                if k in ("total_scraped", "entity_type", "reference")
            }
        )

    service = CssfDiscoveryService(
        session_factory=sf,
        config=cfg.cssf_discovery,
        on_progress=_progress,
    )

    def _worker() -> None:
        progress.start(run_id)
        try:
            service.run(
                entity_types=auth_types,
                mode=mode,  # type: ignore[arg-type]
                triggered_by="USER_UI",
                existing_run_id=run_id,
            )
            with sf() as s:
                r = s.get(DiscoveryRun, run_id)
                progress.finish(r.status if r else "FAILED")
        except Exception as e:  # noqa: BLE001
            progress.finish("FAILED", error=str(e))
            with sf() as s:
                r = s.get(DiscoveryRun, run_id)
                if r is not None:
                    r.status = "FAILED"
                    r.finished_at = datetime.now(UTC)
                    r.error_summary = str(e)
                    s.commit()

    threading.Thread(target=_worker, daemon=True).start()
    return RedirectResponse(f"/discovery/runs/{run_id}", status_code=303)


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
