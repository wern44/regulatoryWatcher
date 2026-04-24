# Schedules Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all 4 scheduled processes into a dedicated `/settings/schedules` page with an overview table, edit cards, process descriptions, and two new schedulable jobs (CSSF Discovery incremental + Catalog Refresh & Analysis).

**Architecture:** Refactor `SchedulerManager` to accept a `dict[str, Callable]` of job callbacks instead of named params. Add discovery + analysis callbacks to the lifespan. Create a new route module `schedules.py` and template `settings/schedules.html`. Remove the two scattered schedule sections from the main settings page. Add a sidebar link.

**Tech Stack:** APScheduler 3.x, FastAPI, SQLAlchemy, Jinja2/HTMX/Tailwind, pytest

---

### Task 1: Refactor SchedulerManager to dict-based constructor with 4 job IDs

**Files:**
- Modify: `regwatch/scheduler/jobs.py`
- Modify: `tests/unit/test_scheduler_jobs.py`

- [ ] **Step 1: Write tests for dict-based constructor and 4 job IDs**

Replace `tests/unit/test_scheduler_jobs.py`:

```python
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from regwatch.scheduler.jobs import FREQUENCY_OPTIONS, SchedulerManager


@pytest.fixture()
def manager():
    scheduler = BackgroundScheduler(timezone="UTC")
    mgr = SchedulerManager(
        scheduler=scheduler,
        jobs={
            SchedulerManager.PIPELINE_JOB_ID: MagicMock(),
            SchedulerManager.DISCOVERY_JOB_ID: MagicMock(),
            SchedulerManager.RECONCILIATION_JOB_ID: MagicMock(),
            SchedulerManager.ANALYSIS_JOB_ID: MagicMock(),
        },
    )
    scheduler.start()
    yield mgr
    if scheduler.running:
        scheduler.shutdown(wait=False)


def test_all_four_job_ids_exist():
    assert hasattr(SchedulerManager, "PIPELINE_JOB_ID")
    assert hasattr(SchedulerManager, "DISCOVERY_JOB_ID")
    assert hasattr(SchedulerManager, "RECONCILIATION_JOB_ID")
    assert hasattr(SchedulerManager, "ANALYSIS_JOB_ID")


def test_four_jobs_coexist(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    manager.apply_schedule(SchedulerManager.DISCOVERY_JOB_ID, "weekly", "05:30")
    manager.apply_schedule(SchedulerManager.RECONCILIATION_JOB_ID, "weekly", "05:00")
    manager.apply_schedule(SchedulerManager.ANALYSIS_JOB_ID, "monthly", "04:00")
    jobs = manager._scheduler.get_jobs()
    assert len(jobs) == 4


def test_apply_schedule_creates_job(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert job is not None


def test_apply_schedule_replaces_existing_job(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "weekly", "10:00")
    jobs = [j for j in manager._scheduler.get_jobs() if j.id == SchedulerManager.PIPELINE_JOB_ID]
    assert len(jobs) == 1


def test_pause_and_resume(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None
    manager.resume(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is not None


def test_pause_one_job_doesnt_affect_other(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.apply_schedule(SchedulerManager.DISCOVERY_JOB_ID, "weekly", "05:30")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None
    assert manager.next_run_time(SchedulerManager.DISCOVERY_JOB_ID) is not None


def test_next_run_time_returns_none_when_no_job(manager: SchedulerManager):
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None


def test_next_run_time_returns_datetime_when_scheduled(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    assert isinstance(manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID), datetime)


def test_4h_uses_interval_trigger(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "4h", "00:00")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert "interval" in str(type(job.trigger)).lower()


def test_daily_uses_cron_trigger(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "14:30")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert "cron" in str(type(job.trigger)).lower()


def test_frequency_options_has_all_keys():
    assert set(FREQUENCY_OPTIONS.keys()) == {"4h", "daily", "2days", "weekly", "monthly"}


def test_is_running_false_when_no_job(manager: SchedulerManager):
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is False


def test_is_running_true_after_apply(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is True


def test_is_running_false_after_pause(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is False
```

- [ ] **Step 2: Run tests — expect failures**

Run: `pytest tests/unit/test_scheduler_jobs.py -v`
Expected: FAIL — `DISCOVERY_JOB_ID`, `ANALYSIS_JOB_ID` don't exist, constructor doesn't accept `jobs`.

- [ ] **Step 3: Update `SchedulerManager`**

Replace `regwatch/scheduler/jobs.py`. Changes from current:
- Add `DISCOVERY_JOB_ID = "scheduled_discovery"` and `ANALYSIS_JOB_ID = "scheduled_analysis"`
- Constructor: replace `pipeline_fn` + `reconciliation_fn` with `jobs: dict[str, Callable[[], None]]`
- Store `self._fns = dict(jobs)` directly
- All methods unchanged (already take `job_id`)
- Update docstring

```python
"""APScheduler-based pipeline scheduler.

A single ``SchedulerManager`` wraps a ``BackgroundScheduler`` and exposes
apply / pause / resume controls.  It manages named jobs whose triggers
are derived from user-chosen frequency strings.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

FREQUENCY_OPTIONS: dict[str, str] = {
    "4h": "Every 4 hours",
    "daily": "Daily",
    "2days": "Every 2 days",
    "weekly": "Weekly",
    "monthly": "Monthly",
}


def _build_trigger(
    frequency: str, time_str: str, timezone: str
) -> IntervalTrigger | CronTrigger:
    """Return the APScheduler trigger for *frequency* and *time_str* (HH:MM)."""
    hour, minute = (int(p) for p in time_str.split(":"))
    if frequency == "4h":
        return IntervalTrigger(hours=4, timezone=timezone)
    if frequency == "daily":
        return CronTrigger(hour=hour, minute=minute, timezone=timezone)
    if frequency == "2days":
        return CronTrigger(hour=hour, minute=minute, day="*/2", timezone=timezone)
    if frequency == "weekly":
        return CronTrigger(day_of_week="mon", hour=hour, minute=minute, timezone=timezone)
    if frequency == "monthly":
        return CronTrigger(day=1, hour=hour, minute=minute, timezone=timezone)
    raise ValueError(f"Unknown frequency: {frequency!r}")


class SchedulerManager:
    """Manages named scheduled jobs."""

    PIPELINE_JOB_ID = "scheduled_pipeline_run"
    DISCOVERY_JOB_ID = "scheduled_discovery"
    RECONCILIATION_JOB_ID = "scheduled_reconciliation"
    ANALYSIS_JOB_ID = "scheduled_analysis"

    def __init__(
        self,
        *,
        scheduler: BackgroundScheduler,
        jobs: dict[str, Callable[[], None]],
    ) -> None:
        self._scheduler = scheduler
        self._fns: dict[str, Callable[[], None]] = dict(jobs)
        self._timezone: str = str(scheduler.timezone)

    def apply_schedule(self, job_id: str, frequency: str, time_str: str) -> None:
        """Remove any existing job and add a new one with the given trigger."""
        existing = self._scheduler.get_job(job_id)
        if existing is not None:
            self._scheduler.remove_job(job_id)
        trigger = _build_trigger(frequency, time_str, self._timezone)
        self._scheduler.add_job(
            self._fns[job_id],
            trigger=trigger,
            id=job_id,
            name=job_id.replace("_", " ").title(),
            max_instances=1,
            replace_existing=True,
        )
        logger.info("Scheduled %s: frequency=%s, time=%s", job_id, frequency, time_str)

    def pause(self, job_id: str) -> None:
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.pause_job(job_id)
            logger.info("Job %s paused", job_id)

    def resume(self, job_id: str) -> None:
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.resume_job(job_id)
            logger.info("Job %s resumed", job_id)

    def next_run_time(self, job_id: str) -> datetime | None:
        job = self._scheduler.get_job(job_id)
        if job is None:
            return None
        return job.next_run_time

    def is_running(self, job_id: str) -> bool:
        job = self._scheduler.get_job(job_id)
        if job is None:
            return False
        return job.next_run_time is not None
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_scheduler_jobs.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add regwatch/scheduler/jobs.py tests/unit/test_scheduler_jobs.py
git commit -m "refactor(scheduler): dict-based constructor with 4 job IDs

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add discovery + analysis callbacks and update lifespan

**Files:**
- Modify: `regwatch/main.py`

- [ ] **Step 1: Rewrite the lifespan**

In `regwatch/main.py`, replace the entire lifespan block (lines 72-162). The key changes:
1. Add `_scheduled_discovery()` and `_scheduled_analysis()` callbacks
2. Change `SchedulerManager` constructor to `jobs={...}` dict
3. Read 4 sets of DB settings instead of 2
4. Apply and conditionally pause 4 jobs

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from apscheduler.schedulers.background import BackgroundScheduler  # noqa: PLC0415
        from regwatch.db.models import AuthorizationType  # noqa: PLC0415
        from regwatch.pipeline.run_helpers import run_pipeline_background  # noqa: PLC0415
        from regwatch.services.cssf_discovery import CssfDiscoveryService  # noqa: PLC0415
        from regwatch.services.discovery import DiscoveryService  # noqa: PLC0415

        bg_scheduler = BackgroundScheduler(timezone=config.ui.timezone)
        pipeline_progress = PipelineProgress()

        def _any_process_running() -> bool:
            if pipeline_progress.snapshot()["status"] == "running":
                return True
            dp = getattr(app.state, "cssf_discovery_progress", None)
            if dp and getattr(dp, "status", "idle") == "running":
                return True
            return False

        def _scheduled_pipeline() -> None:
            if _any_process_running():
                logger.info("Scheduled pipeline skipped — another process running")
                return
            from datetime import UTC, datetime as dt  # noqa: PLC0415
            pipeline_progress.reset_for_run(total_sources=0)
            pipeline_progress.message = "Scheduled pipeline run starting..."
            pipeline_progress.started_at = dt.now(UTC)
            run_pipeline_background(
                session_factory=session_factory,
                config=config,
                llm_client=app.state.llm_client,
                progress=pipeline_progress,
            )

        def _scheduled_discovery() -> None:
            if _any_process_running():
                logger.info("Scheduled discovery skipped — another process running")
                return
            logger.info("Scheduled CSSF discovery (incremental) starting")
            try:
                auth_types = [
                    AuthorizationType(a.type) for a in config.entity.authorizations
                ]
                service = CssfDiscoveryService(
                    session_factory=session_factory,
                    config=config.cssf_discovery,
                )
                service.run(
                    entity_types=auth_types,
                    mode="incremental",
                    triggered_by="SCHEDULER",
                )
                logger.info("Scheduled CSSF discovery completed")
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled CSSF discovery failed")

        def _scheduled_reconciliation() -> None:
            if _any_process_running():
                logger.info("Scheduled reconciliation skipped — another process running")
                return
            logger.info("Scheduled CSSF reconciliation (full) starting")
            try:
                auth_types = [
                    AuthorizationType(a.type) for a in config.entity.authorizations
                ]
                service = CssfDiscoveryService(
                    session_factory=session_factory,
                    config=config.cssf_discovery,
                )
                service.run(
                    entity_types=auth_types,
                    mode="full",
                    triggered_by="SCHEDULER",
                )
                logger.info("Scheduled CSSF reconciliation completed")
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled CSSF reconciliation failed")

        def _scheduled_analysis() -> None:
            if _any_process_running():
                logger.info("Scheduled analysis skipped — another process running")
                return
            logger.info("Scheduled catalog refresh & analysis starting")
            try:
                auth_types = [a.type for a in config.entity.authorizations]
                with session_factory() as s:
                    svc = DiscoveryService(s, llm=app.state.llm_client)
                    svc.classify_catalog()
                    svc.discover_missing(auth_types)
                    s.commit()
                logger.info("Scheduled catalog refresh & analysis completed")
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled catalog refresh & analysis failed")

        SM = SchedulerManager
        scheduler_manager = SchedulerManager(
            scheduler=bg_scheduler,
            jobs={
                SM.PIPELINE_JOB_ID: _scheduled_pipeline,
                SM.DISCOVERY_JOB_ID: _scheduled_discovery,
                SM.RECONCILIATION_JOB_ID: _scheduled_reconciliation,
                SM.ANALYSIS_JOB_ID: _scheduled_analysis,
            },
        )

        # DB key prefix -> (job_id, default_enabled, default_freq, default_time)
        job_config = {
            "scheduler_": (SM.PIPELINE_JOB_ID, "true", "2days", "06:00"),
            "discovery_": (SM.DISCOVERY_JOB_ID, "true", "weekly", "05:30"),
            "reconciliation_": (SM.RECONCILIATION_JOB_ID, "true", "weekly", "05:00"),
            "analysis_": (SM.ANALYSIS_JOB_ID, "false", "monthly", "04:00"),
        }
        with session_factory() as session:
            svc = SettingsService(session)
            for prefix, (job_id, def_enabled, def_freq, def_time) in job_config.items():
                enabled = svc.get(f"{prefix}enabled", def_enabled) or def_enabled
                freq = svc.get(f"{prefix}frequency", def_freq) or def_freq
                time = svc.get(f"{prefix}time", def_time) or def_time
                scheduler_manager.apply_schedule(job_id, freq, time)
                if enabled != "true":
                    scheduler_manager.pause(job_id)

        bg_scheduler.start()
        app.state.scheduler_manager = scheduler_manager
        app.state.pipeline_progress = pipeline_progress
        yield
        if bg_scheduler.running:
            bg_scheduler.shutdown(wait=False)
```

- [ ] **Step 2: Run smoke tests**

Run: `pytest tests/integration/test_app_smoke.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add regwatch/main.py
git commit -m "feat(scheduler): add discovery + analysis callbacks, wire 4 jobs in lifespan

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Create the dedicated schedules page (route + template)

**Files:**
- Create: `regwatch/web/routes/schedules.py`
- Create: `regwatch/web/templates/settings/schedules.html`
- Modify: `regwatch/main.py` (register router)
- Test: `tests/integration/test_schedules_page.py`

- [ ] **Step 1: Write integration tests**

Create `tests/integration/test_schedules_page.py`:

```python
import shutil
from pathlib import Path

import yaml
from fastapi.testclient import TestClient


def _build_config(tmp_path: Path) -> Path:
    shutil.copy("config.example.yaml", tmp_path / "config.yaml")
    cfg_path = tmp_path / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["paths"]["db_file"] = str(tmp_path / "app.db")
    data["paths"]["pdf_archive"] = str(tmp_path / "pdfs")
    data["paths"]["uploads_dir"] = str(tmp_path / "uploads")
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "uploads").mkdir()
    cfg_path.write_text(yaml.safe_dump(data))
    return cfg_path


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    cfg_path = _build_config(tmp_path)
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))
    import importlib
    import regwatch.main as main_module
    importlib.reload(main_module)
    app = main_module.create_app()
    app.state.llm_client.chat_model = "test-model"
    return TestClient(app)


def test_schedules_page_renders(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/settings/schedules")
    assert resp.status_code == 200
    assert "Scheduled Processes" in resp.text
    assert "Pipeline Run" in resp.text
    assert "CSSF Discovery" in resp.text
    assert "Full Reconciliation" in resp.text
    assert "Catalog Refresh" in resp.text


def test_save_schedule_for_pipeline(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/schedules/save",
        data={
            "job": "pipeline",
            "enabled": "true",
            "frequency": "daily",
            "time": "07:00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings/schedules"


def test_save_schedule_for_analysis(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/schedules/save",
        data={
            "job": "analysis",
            "frequency": "monthly",
            "time": "04:00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
```

- [ ] **Step 2: Create the schedules route module**

Create `regwatch/web/routes/schedules.py`:

```python
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

        # Query last pipeline run
        last_pipeline = (
            session.query(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .first()
        )
        # Query last discovery run (incremental or full, for each job)
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
            "analysis": None,  # no run model for analysis yet
        }

        for meta in JOB_META:
            key = meta["key"]
            prefix, job_id = _JOB_MAP[key]
            enabled = svc.get(f"{prefix}enabled", meta["default_enabled"]) == "true"
            freq = svc.get(f"{prefix}frequency", meta["default_freq"]) or meta["default_freq"]
            time = svc.get(f"{prefix}time", meta["default_time"]) or meta["default_time"]
            next_run = scheduler_manager.next_run_time(job_id) if scheduler_manager else None

            freq_label = FREQUENCY_OPTIONS.get(freq, freq)
            if freq != "4h":
                freq_display = f"{freq_label} at {time}"
            else:
                freq_display = freq_label

            last_run = last_runs_map.get(key)

            jobs_data.append({
                "key": key,
                "label": meta["label"],
                "description": meta["description"],
                "enabled": enabled,
                "frequency": freq,
                "time": time,
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

    manager = request.app.state.scheduler_manager
    manager.apply_schedule(job_id, frequency, time)
    if is_enabled:
        manager.resume(job_id)
    else:
        manager.pause(job_id)

    return RedirectResponse(url="/settings/schedules", status_code=303)
```

- [ ] **Step 3: Create the schedules template**

Create `regwatch/web/templates/settings/schedules.html`:

```html
{% extends "base.html" %}
{% block title %}RegWatch — Schedules{% endblock %}
{% block content %}
  <div class="flex items-center justify-between mb-4">
    <div>
      <h1 class="text-2xl font-bold">Scheduled Processes</h1>
      <p class="text-sm text-slate-500 mt-1">
        Current server time: <strong>{{ server_time }}</strong> ({{ server_timezone }})
      </p>
    </div>
    <a href="/settings" class="text-sm text-blue-700 hover:underline">&larr; Back to Settings</a>
  </div>

  {# ── Overview table ── #}
  <section class="bg-white rounded shadow-sm border mb-6 overflow-hidden">
    <table class="w-full text-sm">
      <thead>
        <tr class="bg-slate-50 border-b text-left text-xs uppercase text-slate-500">
          <th class="px-4 py-3">Process</th>
          <th class="px-4 py-3">Status</th>
          <th class="px-4 py-3">Frequency</th>
          <th class="px-4 py-3">Next Run</th>
          <th class="px-4 py-3">Last Result</th>
        </tr>
      </thead>
      <tbody>
        {% for j in jobs %}
        <tr class="border-b last:border-0 {% if not j.enabled %}text-slate-400{% endif %}">
          <td class="px-4 py-3">
            <div class="font-semibold {% if not j.enabled %}text-slate-400{% else %}text-slate-800{% endif %}">{{ j.label }}</div>
            <div class="text-xs text-slate-500">{{ j.description }}</div>
          </td>
          <td class="px-4 py-3">
            {% if j.enabled %}
            <span class="bg-green-100 text-green-800 text-xs font-semibold px-2 py-0.5 rounded-full">Active</span>
            {% else %}
            <span class="bg-amber-100 text-amber-800 text-xs font-semibold px-2 py-0.5 rounded-full">Paused</span>
            {% endif %}
          </td>
          <td class="px-4 py-3">{{ j.freq_display }}</td>
          <td class="px-4 py-3">
            {% if j.enabled and j.next_run %}
              {{ j.next_run.strftime('%Y-%m-%d %H:%M') }}
            {% else %}
              <span class="text-slate-400">&mdash;</span>
            {% endif %}
          </td>
          <td class="px-4 py-3">
            {% if j.last_run %}
              {% set r = j.last_run %}
              {% if r.status is defined %}
                <span class="font-medium {% if r.status == 'COMPLETED' or r.status == 'SUCCESS' %}text-green-700{% elif r.status == 'COMPLETED_WITH_ERRORS' or r.status == 'PARTIAL' %}text-amber-600{% else %}text-red-700{% endif %}">
                  {{ r.status }}
                </span>
                <span class="text-xs text-slate-500 ml-1">{{ r.started_at.strftime('%m-%d %H:%M') if r.started_at else '' }}</span>
                {% if r.events_created is defined %}
                  — {{ r.events_created }} events
                {% elif r.new_count is defined %}
                  — {{ r.new_count or 0 }} new
                {% endif %}
              {% endif %}
            {% else %}
              <span class="text-slate-400">Never</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </section>

  {# ── Edit cards (2×2 grid) ── #}
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
    {% for j in jobs %}
    <section class="bg-white p-4 rounded shadow-sm border {% if not j.enabled %}opacity-60{% endif %}">
      <form method="post" action="/settings/schedules/save">
        <input type="hidden" name="job" value="{{ j.key }}">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-sm font-semibold">{{ j.label }}</h3>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" name="enabled" value="true"
                   {% if j.enabled %}checked{% endif %}
                   class="sr-only peer">
            <div class="w-9 h-5 bg-gray-200 peer-focus:ring-2 peer-focus:ring-blue-300
                        rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white
                        after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white
                        after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4
                        after:transition-all peer-checked:bg-blue-600"></div>
          </label>
        </div>
        <div class="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label class="block text-xs text-slate-500 mb-1">Frequency</label>
            <select name="frequency" class="w-full border rounded px-2 py-1.5 text-sm">
              {% for value, label in frequency_options.items() %}
              <option value="{{ value }}" {% if value == j.frequency %}selected{% endif %}>{{ label }}</option>
              {% endfor %}
            </select>
          </div>
          <div>
            <label class="block text-xs text-slate-500 mb-1">Time</label>
            <input type="time" name="time" value="{{ j.time }}" class="w-full border rounded px-2 py-1.5 text-sm">
          </div>
        </div>
        <button type="submit" class="px-3 py-1.5 bg-slate-800 text-white rounded text-xs hover:bg-slate-700">Save</button>
      </form>
    </section>
    {% endfor %}
  </div>

  {# ── Process descriptions ── #}
  <section class="bg-white p-4 rounded shadow-sm border">
    <h3 class="text-sm font-semibold mb-3">How these processes work together</h3>
    <div class="space-y-3 text-sm text-slate-600">
      <div class="flex gap-3">
        <span class="bg-blue-100 text-blue-800 rounded-full w-6 h-6 flex items-center justify-center text-xs font-bold flex-shrink-0">1</span>
        <p><strong>CSSF Discovery</strong> scrapes the CSSF website for new circulars, laws, and regulations and adds them to the Catalog. Run this first so the Pipeline has a complete catalog to match against.</p>
      </div>
      <div class="flex gap-3">
        <span class="bg-blue-100 text-blue-800 rounded-full w-6 h-6 flex items-center justify-center text-xs font-bold flex-shrink-0">2</span>
        <p><strong>Pipeline Run</strong> checks RSS feeds and SPARQL endpoints for new publications, matches them to catalog entries, and creates Inbox events. This is your main update monitor.</p>
      </div>
      <div class="flex gap-3">
        <span class="bg-blue-100 text-blue-800 rounded-full w-6 h-6 flex items-center justify-center text-xs font-bold flex-shrink-0">3</span>
        <p><strong>Full Reconciliation</strong> walks every page of the CSSF site and retires regulations no longer listed. Run weekly to keep the catalog clean.</p>
      </div>
      <div class="flex gap-3">
        <span class="bg-blue-100 text-blue-800 rounded-full w-6 h-6 flex items-center justify-center text-xs font-bold flex-shrink-0">4</span>
        <p><strong>Catalog Refresh &amp; Analysis</strong> uses the LLM to classify regulations (ICT/DORA, entity applicability) and discover missing ones. Run monthly &mdash; it is LLM-intensive.</p>
      </div>
    </div>
    <p class="text-xs text-slate-400 mt-3">
      Processes never run simultaneously &mdash; each waits for the previous one to finish.
      If a process is already running when another is scheduled, the tick is skipped.
    </p>
  </section>
{% endblock %}
```

- [ ] **Step 4: Register the router in `main.py`**

In `regwatch/main.py`, add the import and include the router. After the existing settings import block (around line 219-221):

```python
    from regwatch.web.routes import (
        schedules as schedules_routes,
    )
```

And after `app.include_router(settings_routes.router)` (around line 231):

```python
    app.include_router(schedules_routes.router)
```

- [ ] **Step 5: Run integration tests**

Run: `pytest tests/integration/test_schedules_page.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/routes/schedules.py regwatch/web/templates/settings/schedules.html regwatch/main.py tests/integration/test_schedules_page.py
git commit -m "feat(ui): dedicated schedules page with overview table and edit cards

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Clean up settings page and add sidebar link

**Files:**
- Modify: `regwatch/web/templates/settings.html`
- Modify: `regwatch/web/templates/partials/sidebar.html`
- Modify: `regwatch/web/routes/settings.py`

- [ ] **Step 1: Remove schedule sections from `settings.html`**

In `regwatch/web/templates/settings.html`, remove:
1. The entire "Scheduled Updates" `<section>` (including the `{% if last_runs %}` block inside it)
2. The entire "Scheduled Reconciliation" `<section>` (including the `{% if last_discovery_runs %}` block)

Replace both with a single link card:

```html
  <section class="bg-white p-4 rounded shadow-sm border mb-4">
    <div class="flex justify-between items-center">
      <div>
        <h2 class="text-lg font-semibold">Scheduled Processes</h2>
        <p class="text-sm text-slate-500">Pipeline, CSSF Discovery, Reconciliation, Analysis</p>
      </div>
      <a href="/settings/schedules" class="px-3 py-2 bg-slate-800 text-white rounded text-sm hover:bg-slate-700">
        Manage schedules
      </a>
    </div>
  </section>
```

- [ ] **Step 2: Clean up `settings.py` route**

In `regwatch/web/routes/settings.py`:

1. Remove the `save_schedule` route (lines 170-195)
2. Remove the `save_reconciliation_schedule` route (lines 198-225)
3. In `settings_view`, remove the scheduler-related context variables from the template dict: `sched_enabled`, `sched_freq`, `sched_time`, `next_run`, `last_runs`, `frequency_options`, `recon_enabled`, `recon_freq`, `recon_time`, `recon_next_run`, `last_discovery_runs`
4. Remove the DB queries for scheduler settings and last_runs/last_discovery_runs from `settings_view`
5. Remove the imports that are no longer needed: `FREQUENCY_OPTIONS`, `SchedulerManager`, `DiscoveryRun`
6. Keep `server_time` and `server_timezone` only if used elsewhere — check and remove if not

- [ ] **Step 3: Add sidebar link**

In `regwatch/web/templates/partials/sidebar.html`, add after the "Extraction Fields" link (line 14):

```html
    <a href="/settings/schedules" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">Schedules</a>
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS (some old schedule tests may need updating — check `test_schedule_settings.py`)

- [ ] **Step 5: Update old schedule integration tests**

If `tests/integration/test_schedule_settings.py` has tests for `save-schedule` or `save-reconciliation-schedule` routes that now 404, update them to use the new `/settings/schedules/save` endpoint, or remove them since `test_schedules_page.py` covers the same functionality.

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/templates/settings.html regwatch/web/templates/partials/sidebar.html regwatch/web/routes/settings.py tests/
git commit -m "refactor(ui): replace schedule sections on settings with link to dedicated page

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Final cleanup, lint, and verification

**Files:**
- Review: all changed files

- [ ] **Step 1: Run linting**

Run: `ruff check regwatch --fix`

- [ ] **Step 2: Run type checking**

Run: `mypy regwatch`

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Start dev server and verify UI**

Run: `uvicorn regwatch.main:app --reload`

Verify at `http://127.0.0.1:8001`:
1. Sidebar shows "Schedules" sub-link under Settings
2. `/settings` shows "Scheduled Processes" link card (no longer inline schedule forms)
3. `/settings/schedules` shows the overview table with 4 processes
4. Each edit card has a working toggle, frequency dropdown, time input, and Save button
5. Saving a schedule redirects back to `/settings/schedules` and shows updated values
6. "How these processes work together" section shows 4 numbered descriptions
7. Server time is displayed correctly

- [ ] **Step 5: Commit any remaining fixes**

```bash
git add -u
git commit -m "chore: lint and cleanup for schedules page

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```
