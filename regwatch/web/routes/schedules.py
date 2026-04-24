"""Schedules sub-page: configure all 4 scheduled processes in one view."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.db.models import DiscoveryRun, PipelineRun
from regwatch.scheduler.jobs import FREQUENCY_OPTIONS, SchedulerManager
from regwatch.services.settings import SettingsService

router = APIRouter(prefix="/settings", tags=["schedules"])

# Maps the form's "job" field to (DB key prefix, SchedulerManager job ID).
_JOB_MAP: dict[str, tuple[str, str]] = {
    "pipeline": ("scheduler_", SchedulerManager.PIPELINE_JOB_ID),
    "discovery": ("discovery_", SchedulerManager.DISCOVERY_JOB_ID),
    "reconciliation": ("reconciliation_", SchedulerManager.RECONCILIATION_JOB_ID),
    "analysis": ("analysis_", SchedulerManager.ANALYSIS_JOB_ID),
}

# Metadata for the overview table and cards.
JOB_META: list[dict[str, str]] = [
    {
        "key": "pipeline",
        "label": "Pipeline Run",
        "description": "Checks RSS/SPARQL sources for new publications",
        "default_freq": "2days",
        "default_time": "06:00",
        "default_enabled": "true",
    },
    {
        "key": "discovery",
        "label": "CSSF Discovery",
        "description": "Incremental scrape of CSSF site for new regulations",
        "default_freq": "weekly",
        "default_time": "05:30",
        "default_enabled": "true",
    },
    {
        "key": "reconciliation",
        "label": "Full Reconciliation",
        "description": "Full CSSF crawl + auto-retire of removed regulations",
        "default_freq": "weekly",
        "default_time": "05:00",
        "default_enabled": "true",
    },
    {
        "key": "analysis",
        "label": "Catalog Refresh & Analysis",
        "description": "LLM classification + missing regulation discovery",
        "default_freq": "monthly",
        "default_time": "04:00",
        "default_enabled": "false",
    },
]


@router.get("/schedules", response_class=HTMLResponse)
def schedules_page(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    config = request.app.state.config
    scheduler_manager = getattr(request.app.state, "scheduler_manager", None)

    tz = ZoneInfo(config.ui.timezone)
    server_time = datetime.now(tz).strftime("%H:%M")

    jobs_data: list[dict] = []
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)

        last_pipeline = (
            session.query(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .first()
        )
        last_discovery_incr = (
            session.query(DiscoveryRun)
            .filter(DiscoveryRun.mode == "incremental")
            .order_by(DiscoveryRun.started_at.desc())
            .first()
        )
        last_discovery_full = (
            session.query(DiscoveryRun)
            .filter(DiscoveryRun.mode == "full")
            .order_by(DiscoveryRun.started_at.desc())
            .first()
        )

        last_runs_map = {
            "pipeline": last_pipeline,
            "discovery": last_discovery_incr,
            "reconciliation": last_discovery_full,
            "analysis": None,
        }

        for meta in JOB_META:
            key = meta["key"]
            prefix, job_id = _JOB_MAP[key]
            enabled = (
                svc.get(f"{prefix}enabled", meta["default_enabled"])
                == "true"
            )
            freq = (
                svc.get(f"{prefix}frequency", meta["default_freq"])
                or meta["default_freq"]
            )
            time_val = (
                svc.get(f"{prefix}time", meta["default_time"])
                or meta["default_time"]
            )
            next_run = (
                scheduler_manager.next_run_time(job_id)
                if scheduler_manager
                else None
            )

            freq_label = FREQUENCY_OPTIONS.get(freq, freq)
            if freq != "4h":
                freq_display = f"{freq_label} at {time_val}"
            else:
                freq_display = freq_label

            last_run = last_runs_map.get(key)

            jobs_data.append({
                "key": key,
                "label": meta["label"],
                "description": meta["description"],
                "enabled": enabled,
                "frequency": freq,
                "time": time_val,
                "freq_display": freq_display,
                "next_run": next_run,
                "last_run": last_run,
            })

    return templates.TemplateResponse(
        request,
        "settings/schedules.html",
        {
            "active": "settings",
            "jobs": jobs_data,
            "frequency_options": FREQUENCY_OPTIONS,
            "server_time": server_time,
            "server_timezone": config.ui.timezone,
        },
    )


@router.post("/schedules/save")
def save_schedule(
    request: Request,
    job: str = Form(...),
    frequency: str = Form(...),
    time: str = Form("06:00"),
    enabled: str | None = Form(None),
) -> RedirectResponse:
    if job not in _JOB_MAP:
        return RedirectResponse(url="/settings/schedules", status_code=303)

    prefix, job_id = _JOB_MAP[job]
    is_enabled = enabled is not None

    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        svc.set(f"{prefix}enabled", "true" if is_enabled else "false")
        svc.set(f"{prefix}frequency", frequency)
        svc.set(f"{prefix}time", time)
        session.commit()

    manager = getattr(request.app.state, "scheduler_manager", None)
    if manager is not None:
        manager.apply_schedule(job_id, frequency, time)
        if is_enabled:
            manager.resume(job_id)
        else:
            manager.pause(job_id)

    return RedirectResponse(url="/settings/schedules", status_code=303)
