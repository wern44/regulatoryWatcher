"""Dashboard route: KPIs + upcoming deadlines widget."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.deadlines import DeadlineService
from regwatch.services.inbox import InboxService
from regwatch.services.regulations import RegulationFilter, RegulationService

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        reg_svc = RegulationService(session)
        inbox_svc = InboxService(session)
        deadline_svc = DeadlineService(session)

        all_regs = reg_svc.list(RegulationFilter())
        ict_regs = reg_svc.list(RegulationFilter(is_ict=True))
        drafts = reg_svc.list(
            RegulationFilter(
                lifecycle_stages=[
                    "CONSULTATION",
                    "PROPOSAL",
                    "DRAFT_BILL",
                    "ADOPTED_NOT_IN_FORCE",
                ]
            )
        )
        upcoming = deadline_svc.upcoming(window_days=730)
        inbox_count = inbox_svc.count_new()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "kpis": {
                "catalog": len(
                    [r for r in all_regs if r.lifecycle_stage == "IN_FORCE"]
                ),
                "inbox": inbox_count,
                "drafts": len(drafts),
                "ict": len(ict_regs),
            },
            "upcoming": upcoming[:5],
            "progress": request.app.state.pipeline_progress.snapshot(),
        },
    )
