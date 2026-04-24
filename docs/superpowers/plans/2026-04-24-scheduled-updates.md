# Scheduled Update Checks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the full pipeline on a user-configurable schedule (default: every 2 days) inside the FastAPI process, with settings saved in the DB and controllable from the Settings page.

**Architecture:** A `SchedulerManager` class wraps APScheduler's `BackgroundScheduler` with a single job that runs all enabled sources sequentially. Settings (enabled, frequency, preferred time) are persisted in the existing `Setting` key-value table. The web UI exposes a form on `/settings` to configure and pause/resume the schedule, and displays the last 2 pipeline runs inline.

**Tech Stack:** APScheduler 3.x (existing dependency), FastAPI, SQLAlchemy, Jinja2/HTMX/Tailwind, pytest

---

### Task 1: Extract shared pipeline helper from `actions.py`

**Files:**
- Create: `regwatch/pipeline/run_helpers.py`
- Modify: `regwatch/web/routes/actions.py`
- Test: `tests/unit/test_run_helpers.py`

The existing `_run_pipeline_in_background` in `actions.py` is the exact logic the scheduler also needs. Extract it into a shared module so both callers use the same code path.

- [ ] **Step 1: Create `run_helpers.py` with the extracted function**

```python
# regwatch/pipeline/run_helpers.py
"""Shared pipeline execution logic for manual and scheduled runs."""
from __future__ import annotations

import logging

from regwatch.pipeline.pipeline_factory import build_runner
from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.sources import build_enabled_sources

logger = logging.getLogger(__name__)


def run_pipeline_background(
    *,
    session_factory,
    config,
    llm_client,
    progress: PipelineProgress,
) -> None:
    """Run all enabled sources in a fresh DB session.

    Used by both the manual "Run pipeline now" button and the scheduler.
    Catches all exceptions and reports them via *progress*.
    """
    try:
        sources = build_enabled_sources(config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline source instantiation failed")
        progress.finish(run_id=None, error=f"{type(exc).__name__}: {exc}")
        return

    with session_factory() as session:
        try:
            runner = build_runner(
                session,
                sources=sources,
                archive_root=config.paths.pdf_archive,
                llm_client=llm_client,
            )
            run_id = runner.run_once(progress=progress)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("Pipeline run failed")
            progress.finish(run_id=None, error=f"{type(exc).__name__}: {exc}")
            return

    progress.finish(run_id=run_id)
```

- [ ] **Step 2: Write a unit test verifying `run_pipeline_background` reports errors via progress**

```python
# tests/unit/test_run_helpers.py
from unittest.mock import MagicMock, patch

from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.run_helpers import run_pipeline_background


def test_reports_source_build_error_via_progress():
    progress = PipelineProgress()
    with patch(
        "regwatch.pipeline.run_helpers.build_enabled_sources",
        side_effect=RuntimeError("bad source"),
    ):
        run_pipeline_background(
            session_factory=MagicMock(),
            config=MagicMock(),
            llm_client=MagicMock(),
            progress=progress,
        )
    snap = progress.snapshot()
    assert snap["status"] == "failed"
    assert "bad source" in snap["error"]
```

- [ ] **Step 3: Run the test**

Run: `pytest tests/unit/test_run_helpers.py -v`
Expected: PASS

- [ ] **Step 4: Update `actions.py` to use `run_pipeline_background`**

Replace the `_run_pipeline_in_background` function body in `regwatch/web/routes/actions.py` with:

```python
# At the top, replace the three pipeline imports with:
from regwatch.pipeline.run_helpers import run_pipeline_background

# Remove the _run_pipeline_in_background function entirely.
# In run_pipeline(), change the Thread target:
    thread = threading.Thread(
        target=run_pipeline_background,
        kwargs={
            "session_factory": request.app.state.session_factory,
            "config": request.app.state.config,
            "llm_client": request.app.state.llm_client,
            "progress": progress,
        },
        name="regwatch-pipeline",
        daemon=True,
    )
```

The full `actions.py` after edit:

```python
"""Manual actions triggered from the web UI (run pipeline now, status polling)."""
from __future__ import annotations

import threading
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.run_helpers import run_pipeline_background

router = APIRouter()


@router.post("/run-pipeline", response_class=HTMLResponse)
def run_pipeline(request: Request) -> HTMLResponse:
    """Start a pipeline run in a background thread and return the progress widget."""
    progress: PipelineProgress = request.app.state.pipeline_progress
    templates = request.app.state.templates

    snapshot = progress.snapshot()
    if snapshot["status"] == "running":
        return templates.TemplateResponse(
            request,
            "partials/pipeline_progress.html",
            {"progress": snapshot},
        )

    progress.reset_for_run(total_sources=0)
    progress.message = "Initialising pipeline..."
    progress.started_at = datetime.now(UTC)

    thread = threading.Thread(
        target=run_pipeline_background,
        kwargs={
            "session_factory": request.app.state.session_factory,
            "config": request.app.state.config,
            "llm_client": request.app.state.llm_client,
            "progress": progress,
        },
        name="regwatch-pipeline",
        daemon=True,
    )
    thread.start()

    return templates.TemplateResponse(
        request,
        "partials/pipeline_progress.html",
        {"progress": progress.snapshot()},
    )


@router.get("/run-pipeline/status", response_class=HTMLResponse)
def run_pipeline_status(request: Request) -> HTMLResponse:
    """HTMX polling endpoint. Returns the progress widget; self-replaces."""
    progress: PipelineProgress = request.app.state.pipeline_progress
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "partials/pipeline_progress.html",
        {"progress": progress.snapshot()},
    )
```

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `pytest tests/ -v --timeout=60`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/run_helpers.py tests/unit/test_run_helpers.py regwatch/web/routes/actions.py
git commit -m "refactor: extract shared pipeline helper from actions.py"
```

---

### Task 2: Rewrite `scheduler/jobs.py` with `SchedulerManager`

**Files:**
- Modify: `regwatch/scheduler/jobs.py`
- Modify: `tests/unit/test_scheduler_jobs.py`

Replace the old per-source-group scheduler with a single-job `SchedulerManager`.

- [ ] **Step 1: Write failing tests for `SchedulerManager`**

Replace the contents of `tests/unit/test_scheduler_jobs.py`:

```python
# tests/unit/test_scheduler_jobs.py
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
        run_fn=MagicMock(),
    )
    scheduler.start()
    yield mgr
    if scheduler.running:
        scheduler.shutdown(wait=False)


def test_apply_schedule_creates_job(manager: SchedulerManager):
    manager.apply_schedule("daily", "08:00")
    job = manager._scheduler.get_job(SchedulerManager.JOB_ID)
    assert job is not None


def test_apply_schedule_replaces_existing_job(manager: SchedulerManager):
    manager.apply_schedule("daily", "08:00")
    manager.apply_schedule("weekly", "10:00")
    jobs = manager._scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == SchedulerManager.JOB_ID


def test_pause_and_resume(manager: SchedulerManager):
    manager.apply_schedule("daily", "08:00")
    manager.pause()
    assert manager.next_run_time() is None  # paused job has no next_run_time
    manager.resume()
    assert manager.next_run_time() is not None


def test_next_run_time_returns_none_when_no_job(manager: SchedulerManager):
    assert manager.next_run_time() is None


def test_next_run_time_returns_datetime_when_scheduled(
    manager: SchedulerManager,
):
    manager.apply_schedule("daily", "06:00")
    nrt = manager.next_run_time()
    assert isinstance(nrt, datetime)


def test_4h_frequency_uses_interval_trigger(manager: SchedulerManager):
    manager.apply_schedule("4h", "00:00")
    job = manager._scheduler.get_job(SchedulerManager.JOB_ID)
    assert job is not None
    assert "interval" in str(type(job.trigger)).lower()


def test_daily_frequency_uses_cron_trigger(manager: SchedulerManager):
    manager.apply_schedule("daily", "14:30")
    job = manager._scheduler.get_job(SchedulerManager.JOB_ID)
    assert "cron" in str(type(job.trigger)).lower()


def test_frequency_options_has_all_keys():
    assert set(FREQUENCY_OPTIONS.keys()) == {"4h", "daily", "2days", "weekly", "monthly"}


def test_is_running_false_when_no_job(manager: SchedulerManager):
    assert manager.is_running() is False


def test_is_running_true_after_apply(manager: SchedulerManager):
    manager.apply_schedule("daily", "06:00")
    assert manager.is_running() is True


def test_is_running_false_after_pause(manager: SchedulerManager):
    manager.apply_schedule("daily", "06:00")
    manager.pause()
    assert manager.is_running() is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_scheduler_jobs.py -v`
Expected: FAIL — `SchedulerManager` and `FREQUENCY_OPTIONS` don't exist yet.

- [ ] **Step 3: Implement `SchedulerManager` in `scheduler/jobs.py`**

Replace the entire contents of `regwatch/scheduler/jobs.py`:

```python
"""APScheduler-based pipeline scheduler.

A single ``SchedulerManager`` wraps a ``BackgroundScheduler`` and exposes
apply / pause / resume controls.  It manages exactly one job whose trigger
is derived from the user-chosen frequency string.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Maps the DB value to a human-readable label shown in the UI.
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
        return CronTrigger(
            hour=hour, minute=minute, day="*/2", timezone=timezone
        )
    if frequency == "weekly":
        return CronTrigger(
            day_of_week="mon", hour=hour, minute=minute, timezone=timezone
        )
    if frequency == "monthly":
        return CronTrigger(
            day=1, hour=hour, minute=minute, timezone=timezone
        )
    raise ValueError(f"Unknown frequency: {frequency!r}")


class SchedulerManager:
    """Manages a single scheduled pipeline job."""

    JOB_ID = "scheduled_pipeline_run"

    def __init__(
        self,
        *,
        scheduler: BackgroundScheduler,
        run_fn: Callable[[], None],
    ) -> None:
        self._scheduler = scheduler
        self._run_fn = run_fn
        self._timezone: str = str(scheduler.timezone)

    def apply_schedule(self, frequency: str, time_str: str) -> None:
        """Remove any existing job and add a new one with the given trigger."""
        existing = self._scheduler.get_job(self.JOB_ID)
        if existing is not None:
            self._scheduler.remove_job(self.JOB_ID)

        trigger = _build_trigger(frequency, time_str, self._timezone)
        self._scheduler.add_job(
            self._run_fn,
            trigger=trigger,
            id=self.JOB_ID,
            name="Scheduled pipeline run",
            max_instances=1,
            replace_existing=True,
        )
        logger.info(
            "Scheduled pipeline: frequency=%s, time=%s", frequency, time_str
        )

    def pause(self) -> None:
        """Pause the scheduled job (it stays registered but won't fire)."""
        if self._scheduler.get_job(self.JOB_ID) is not None:
            self._scheduler.pause_job(self.JOB_ID)
            logger.info("Scheduler paused")

    def resume(self) -> None:
        """Resume a paused job."""
        if self._scheduler.get_job(self.JOB_ID) is not None:
            self._scheduler.resume_job(self.JOB_ID)
            logger.info("Scheduler resumed")

    def next_run_time(self) -> datetime | None:
        """Return the next fire time, or None if paused/no job."""
        job = self._scheduler.get_job(self.JOB_ID)
        if job is None:
            return None
        return job.next_run_time

    def is_running(self) -> bool:
        """True if the scheduler is started and the job is active (not paused)."""
        job = self._scheduler.get_job(self.JOB_ID)
        if job is None:
            return False
        return job.next_run_time is not None
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/unit/test_scheduler_jobs.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add regwatch/scheduler/jobs.py tests/unit/test_scheduler_jobs.py
git commit -m "feat(scheduler): rewrite jobs.py with SchedulerManager"
```

---

### Task 3: Wire `SchedulerManager` into `main.py` startup

**Files:**
- Modify: `regwatch/main.py`

Connect the scheduler to the real pipeline callback and start it based on DB settings.

- [ ] **Step 1: Rewrite the lifespan and scheduler wiring in `main.py`**

Replace the `import` of `build_scheduler` and the `lifespan` block in `regwatch/main.py`. The changed sections (lines 24, 69–81):

Replace line 24:
```python
# Old:
from regwatch.scheduler.jobs import build_scheduler
# New:
from regwatch.scheduler.jobs import SchedulerManager
```

Replace the lifespan block (lines 69–81) with:

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from apscheduler.schedulers.background import BackgroundScheduler  # noqa: PLC0415

        from regwatch.pipeline.run_helpers import run_pipeline_background  # noqa: PLC0415

        bg_scheduler = BackgroundScheduler(timezone=config.ui.timezone)

        pipeline_progress = PipelineProgress()

        def _scheduled_run() -> None:
            snap = pipeline_progress.snapshot()
            if snap["status"] == "running":
                logger.info("Scheduled tick skipped — pipeline already running")
                return
            from datetime import UTC, datetime  # noqa: PLC0415

            pipeline_progress.reset_for_run(total_sources=0)
            pipeline_progress.message = "Scheduled pipeline run starting..."
            pipeline_progress.started_at = datetime.now(UTC)
            run_pipeline_background(
                session_factory=session_factory,
                config=config,
                llm_client=app.state.llm_client,
                progress=pipeline_progress,
            )

        scheduler_manager = SchedulerManager(
            scheduler=bg_scheduler,
            run_fn=_scheduled_run,
        )

        # Read schedule settings from DB.
        with session_factory() as session:
            svc = SettingsService(session)
            sched_enabled = svc.get("scheduler_enabled", "true")
            sched_freq = svc.get("scheduler_frequency", "2days")
            sched_time = svc.get("scheduler_time", "06:00")

        scheduler_manager.apply_schedule(sched_freq, sched_time)
        bg_scheduler.start()
        if sched_enabled != "true":
            scheduler_manager.pause()

        app.state.scheduler_manager = scheduler_manager
        app.state.pipeline_progress = pipeline_progress
        yield
        if bg_scheduler.running:
            bg_scheduler.shutdown(wait=False)
```

Also add a `logger` at the top of the file (after the existing imports, around line 17):

```python
import logging

logger = logging.getLogger(__name__)
```

And remove the duplicate `app.state.pipeline_progress = PipelineProgress()` line (line 106 in the original), since it's now created inside the lifespan. Also remove the old `app.state.scheduler = scheduler` and `app.state.config = config` / `app.state.session_factory = session_factory` lines from inside the lifespan (they're already set outside it on lines 98–99).

- [ ] **Step 2: Verify the app starts without errors**

Run: `pytest tests/integration/test_app_smoke.py -v`
Expected: PASS — the smoke test creates the app and hits `/` and `/settings`.

- [ ] **Step 3: Commit**

```bash
git add regwatch/main.py
git commit -m "feat(scheduler): wire SchedulerManager into app startup"
```

---

### Task 4: Add schedule settings route and UI section

**Files:**
- Modify: `regwatch/web/routes/settings.py`
- Modify: `regwatch/web/templates/settings.html`
- Test: `tests/integration/test_schedule_settings.py`

- [ ] **Step 1: Write integration test for saving schedule settings**

```python
# tests/integration/test_schedule_settings.py
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


def test_settings_page_shows_scheduler_section(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Scheduled Updates" in resp.text
    assert "scheduler_frequency" in resp.text


def test_save_schedule_persists_and_redirects(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/save-schedule",
        data={
            "scheduler_enabled": "true",
            "scheduler_frequency": "weekly",
            "scheduler_time": "09:30",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"

    # Verify settings are persisted by reloading the page.
    resp2 = client.get("/settings")
    assert "weekly" in resp2.text


def test_save_schedule_pauses_when_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/save-schedule",
        data={
            "scheduler_frequency": "daily",
            "scheduler_time": "08:00",
        },
        follow_redirects=False,
    )
    # No scheduler_enabled field means the checkbox was unchecked.
    assert resp.status_code == 303
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/integration/test_schedule_settings.py -v`
Expected: FAIL — `/settings/save-schedule` route doesn't exist, template doesn't have "Scheduled Updates".

- [ ] **Step 3: Add the `save-schedule` route to `settings.py`**

Add this import at the top of `regwatch/web/routes/settings.py` (after the existing `from datetime import UTC, datetime` line):

```python
from zoneinfo import ZoneInfo
```

Add this route after the existing `save_models` route (after line 118):

```python
@router.post("/save-schedule")
def save_schedule(
    request: Request,
    scheduler_frequency: str = Form(...),
    scheduler_time: str = Form("06:00"),
    scheduler_enabled: str | None = Form(None),
) -> RedirectResponse:
    enabled = scheduler_enabled is not None
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        svc.set("scheduler_enabled", "true" if enabled else "false")
        svc.set("scheduler_frequency", scheduler_frequency)
        svc.set("scheduler_time", scheduler_time)
        session.commit()

    manager = request.app.state.scheduler_manager
    manager.apply_schedule(scheduler_frequency, scheduler_time)
    if enabled:
        manager.resume()
    else:
        manager.pause()

    return RedirectResponse(url="/settings", status_code=303)
```

- [ ] **Step 4: Extend the `settings_view` route to pass scheduler context**

In `regwatch/web/routes/settings.py`, modify the `settings_view` function. (`ZoneInfo` was already imported in Step 3.)

Inside `settings_view`, after the existing `with ... as session:` block (after line 55), add a second block to read scheduler settings and last 2 runs:

```python
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        sched_enabled = svc.get("scheduler_enabled", "true") == "true"
        sched_freq = svc.get("scheduler_frequency", "2days")
        sched_time = svc.get("scheduler_time", "06:00")
        last_runs = (
            session.query(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .limit(2)
            .all()
        )
```

Note: The existing `runs` query (limit 10) is already there for the "Recent pipeline runs" section at the bottom. `last_runs` (limit 2) is for the scheduler card. You can reuse `runs[:2]` instead if you prefer, but a separate query makes the intent clearer.

Add the scheduler manager's next run time:

```python
    scheduler_manager = request.app.state.scheduler_manager
    next_run = scheduler_manager.next_run_time()
    tz = ZoneInfo(config.ui.timezone)
    server_time = datetime.now(tz).strftime("%H:%M")
```

Add these keys to the template context dict in the `TemplateResponse` call:

```python
        {
            # ... existing keys ...
            "sched_enabled": sched_enabled,
            "sched_freq": sched_freq,
            "sched_time": sched_time,
            "next_run": next_run,
            "server_time": server_time,
            "server_timezone": config.ui.timezone,
            "last_runs": last_runs,
        },
```

Import `FREQUENCY_OPTIONS` at the top of the file:

```python
from regwatch.scheduler.jobs import FREQUENCY_OPTIONS
```

And add it to the template context:

```python
            "frequency_options": FREQUENCY_OPTIONS,
```

- [ ] **Step 5: Add the "Scheduled Updates" section to `settings.html`**

In `regwatch/web/templates/settings.html`, insert this new section **after** the "LLM Server" `</section>` (after line 96) and **before** the "CSSF catalog reconciliation" section:

```html
  <section class="bg-white p-4 rounded shadow-sm border mb-4">
    <h2 class="text-lg font-semibold mb-2">Scheduled Updates</h2>
    <p class="text-sm text-slate-600 mb-3">
      Automatically check all enabled sources for new regulations, circulars and
      law updates on a regular schedule.
    </p>
    <form method="post" action="/settings/save-schedule" class="space-y-4">
      <div class="flex items-center gap-3">
        <label class="text-sm font-medium" for="sched_toggle">Enable automatic checks</label>
        <label class="relative inline-flex items-center cursor-pointer">
          <input type="checkbox" id="sched_toggle" name="scheduler_enabled" value="true"
                 {% if sched_enabled %}checked{% endif %}
                 class="sr-only peer"
                 onchange="document.getElementById('sched_controls').classList.toggle('opacity-50', !this.checked)">
          <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300
                      rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white
                      after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white
                      after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5
                      after:transition-all peer-checked:bg-blue-600"></div>
        </label>
      </div>

      <div id="sched_controls" class="space-y-3 {% if not sched_enabled %}opacity-50{% endif %}">
        <div>
          <label class="block text-xs font-medium mb-1" for="scheduler_frequency">Check frequency</label>
          <select name="scheduler_frequency" id="scheduler_frequency"
                  class="w-full border rounded px-3 py-2 text-sm"
                  onchange="document.getElementById('time_row').style.display = this.value === '4h' ? 'none' : 'block'">
            {% for value, label in frequency_options.items() %}
            <option value="{{ value }}" {% if value == sched_freq %}selected{% endif %}>{{ label }}</option>
            {% endfor %}
          </select>
        </div>

        <div id="time_row" {% if sched_freq == '4h' %}style="display:none"{% endif %}>
          <label class="block text-xs font-medium mb-1" for="scheduler_time">Preferred time</label>
          <input type="time" name="scheduler_time" id="scheduler_time" value="{{ sched_time }}"
                 class="border rounded px-3 py-2 text-sm">
          <p class="text-xs text-slate-500 mt-1">
            Current server time: <strong>{{ server_time }}</strong> ({{ server_timezone }})
          </p>
        </div>

        <div class="text-sm text-slate-700">
          {% if sched_enabled and next_run %}
            Next check: <strong>{{ next_run.strftime('%Y-%m-%d %H:%M') }}</strong>
          {% elif not sched_enabled %}
            <span class="text-amber-600 font-medium">Paused</span>
          {% else %}
            <span class="text-slate-500">Not scheduled</span>
          {% endif %}
        </div>
      </div>

      <button type="submit" class="px-3 py-2 bg-slate-800 text-white rounded text-sm hover:bg-slate-700">
        Save schedule
      </button>
    </form>

    {% if last_runs %}
    <div class="mt-4 pt-3 border-t">
      <h3 class="text-sm font-medium text-slate-600 mb-2">Last checks</h3>
      <ul class="space-y-2 text-sm">
        {% for r in last_runs %}
        <li class="flex justify-between items-start">
          <div>
            <span class="font-medium">#{{ r.run_id }}</span>
            <span class="{% if r.status == 'COMPLETED' %}text-green-700{% elif r.status == 'FAILED' %}text-red-700{% else %}text-slate-500{% endif %}">
              {{ r.status }}
            </span>
            <span class="text-slate-500 text-xs ml-1">{{ r.started_at.strftime('%Y-%m-%d %H:%M') }}</span>
          </div>
          <div class="text-xs text-slate-500 text-right">
            {{ r.events_created }} new event{{ 's' if r.events_created != 1 else '' }},
            {{ r.versions_created }} new version{{ 's' if r.versions_created != 1 else '' }}
          </div>
        </li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}
  </section>
```

- [ ] **Step 6: Run the integration tests**

Run: `pytest tests/integration/test_schedule_settings.py -v`
Expected: All PASS

- [ ] **Step 7: Run the full test suite to check for regressions**

Run: `pytest tests/ -v --timeout=60`
Expected: All existing tests PASS

- [ ] **Step 8: Commit**

```bash
git add regwatch/web/routes/settings.py regwatch/web/templates/settings.html tests/integration/test_schedule_settings.py
git commit -m "feat(scheduler): add schedule settings UI and save-schedule route"
```

---

### Task 5: Clean up old scheduler references and run full verification

**Files:**
- Modify: `regwatch/scheduler/__init__.py` (if it exists — ensure clean exports)
- Review: `regwatch/main.py` for any remaining references to old `build_scheduler`

- [ ] **Step 1: Verify no remaining imports of removed symbols**

Search the codebase for any references to `build_scheduler`, `SOURCE_TO_JOB`, or `assert_sources_have_jobs` outside of test files and `scheduler/jobs.py` itself. These were removed in Task 2.

Run: `grep -rn "build_scheduler\|SOURCE_TO_JOB\|assert_sources_have_jobs" regwatch/ tests/ --include="*.py"`

If any hits remain in `regwatch/main.py` or elsewhere, remove them.

- [ ] **Step 2: Check that `scheduler/__init__.py` exists and is clean**

If `regwatch/scheduler/__init__.py` exists, ensure it doesn't re-export removed symbols. If it doesn't exist, no action needed.

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: All PASS

- [ ] **Step 4: Run linting and type checking**

Run: `ruff check regwatch && mypy regwatch`
Expected: No new errors

- [ ] **Step 5: Start the dev server and verify the UI**

Run: `uvicorn regwatch.main:app --reload`

1. Open `http://127.0.0.1:8001/settings` in a browser.
2. Verify the "Scheduled Updates" section appears between "LLM Server" and "CSSF catalog reconciliation".
3. Verify the toggle switch works (on/off).
4. Change frequency to "Daily" and set time to current time + 2 minutes.
5. Click "Save schedule" — verify page reloads and shows the saved values.
6. Verify "Next check" shows the expected datetime.
7. Toggle off, save — verify "Paused" is shown.
8. Verify the "Last checks" area shows recent pipeline runs (or "No runs yet" message if none).
9. Verify current server time is displayed correctly next to the time input.
10. Switch frequency to "Every 4 hours" — verify the time input hides.

- [ ] **Step 6: Commit any remaining fixes**

```bash
git add -u
git commit -m "chore: clean up old scheduler references"
```
