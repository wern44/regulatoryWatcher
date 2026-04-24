# Scheduled Update Checks — Design Spec

**Date:** 2026-04-24
**Status:** Approved

## Goal

Run the full pipeline (all enabled sources) on a user-configurable schedule while the web server is running. The user can choose a frequency, preferred time-of-day, and pause/resume from the Settings page. The last two check results are shown inline.

## Constraints

- Scheduler runs inside the FastAPI/uvicorn process (no external service).
- APScheduler is already a dependency and partially wired — reuse it.
- All sources are triggered in a single run, processed sequentially (existing `PipelineRunner.run_once` behavior). No concurrent source execution.
- Settings are persisted in the existing `Setting` key-value table in SQLite.
- SQLite concurrency is handled by the existing `busy_timeout=10000` PRAGMA and `NullPool` configuration.

## Architecture

### Database Settings

Three keys in the existing `Setting` table (managed via `SettingsService`):

| Key | Allowed values | Default |
|-----|---------------|---------|
| `scheduler_enabled` | `"true"`, `"false"` | `"true"` |
| `scheduler_frequency` | `"4h"`, `"daily"`, `"2days"`, `"weekly"`, `"monthly"` | `"2days"` |
| `scheduler_time` | `"HH:MM"` (24h, e.g. `"06:00"`) | `"06:00"` |

No new ORM models are needed. The `SettingsService.get()` / `.set()` API handles reads and writes.

### Frequency-to-trigger mapping

| Frequency value | APScheduler trigger | Behavior |
|----------------|-------------------|----------|
| `"4h"` | `IntervalTrigger(hours=4)` | Runs every 4 hours from startup; `scheduler_time` is ignored |
| `"daily"` | `CronTrigger(hour=H, minute=M)` | Runs once per day at the configured time |
| `"2days"` | `CronTrigger(hour=H, minute=M, day="*/2")` | Runs every 2 days at the configured time |
| `"weekly"` | `CronTrigger(day_of_week="mon", hour=H, minute=M)` | Runs every Monday at the configured time |
| `"monthly"` | `CronTrigger(day=1, hour=H, minute=M)` | Runs on the 1st of each month at the configured time |

The timezone for all triggers is `config.ui.timezone` (from `config.example.yaml`).

### Scheduler module rework (`regwatch/scheduler/jobs.py`)

Replace the current per-source-group system with a `SchedulerManager` class:

```python
class SchedulerManager:
    JOB_ID = "scheduled_pipeline_run"

    def __init__(self, bg_scheduler, session_factory, config, run_fn):
        ...

    def apply_schedule(self, frequency: str, time_str: str) -> None:
        """Remove the existing job and add a new one with the given trigger."""

    def pause(self) -> None:
        """Pause the scheduled job."""

    def resume(self) -> None:
        """Resume the paused job."""

    def next_run_time(self) -> datetime | None:
        """Return the next fire time, or None if paused/disabled."""

    def is_running(self) -> bool:
        """True if the scheduler is started and the job is active."""
```

Key behaviors:
- **Single job** with `id="scheduled_pipeline_run"` and `max_instances=1`.
- **Overlap guard**: The job callback checks `PipelineProgress.snapshot()["status"]`. If a manual or previous scheduled run is still in-flight, the tick is skipped with a log warning.
- **`apply_schedule()`** is called on startup (from DB settings) and when the user saves new settings (from the web route). It removes the old job and adds a new one with the appropriate trigger.
- **`pause()` / `resume()`** use APScheduler's `pause_job()` / `resume_job()`.

### Startup flow (`regwatch/main.py`)

In the `lifespan` context manager:

1. Read `scheduler_enabled`, `scheduler_frequency`, `scheduler_time` from DB via `SettingsService` (with defaults if keys don't exist yet).
2. Create a `BackgroundScheduler(timezone=config.ui.timezone)`.
3. Build a `SchedulerManager` wrapping it, with the real pipeline callback (reusing the `_run_pipeline_in_background` pattern from `actions.py`).
4. Call `manager.apply_schedule(frequency, time)` to register the job.
5. If `scheduler_enabled == "true"`, start the scheduler.
6. Store the `SchedulerManager` on `app.state.scheduler_manager`.
7. On shutdown, call `scheduler.shutdown(wait=False)`.

The current `build_scheduler` function and `SOURCE_TO_JOB` mapping are removed — they are unused dead code that was never activated.

### Pipeline callback

The scheduler's callback function:

1. Check `pipeline_progress.snapshot()["status"]` — if `"running"`, log a skip and return.
2. Reset the progress object.
3. Open a new DB session from `session_factory`.
4. Call `build_enabled_sources(config)` to get all enabled sources.
5. Call `build_runner(session, sources=..., archive_root=..., llm_client=...)`.
6. Call `runner.run_once(progress=pipeline_progress)`.
7. Commit and close the session.

This is essentially the same as `_run_pipeline_in_background` in `actions.py`. The shared logic will be extracted into a helper function in `regwatch/pipeline/run_helpers.py` so both the manual "Run pipeline now" button and the scheduler use the same code path.

### Web UI — Settings page

A new section on `/settings` titled **"Scheduled Updates"**, placed after the "LLM Server" section.

#### Layout

```
+----------------------------------------------------------+
| Scheduled Updates                                        |
|                                                          |
| Enable automatic checks    [=== ON ===]                  |
|                                                          |
| Check frequency            [ Every 2 days       v ]     |
|                                                          |
| Preferred time             [ 06:00 ]                     |
|   Current server time: 14:32 (Europe/Luxembourg)         |
|                                                          |
| Next check: 2026-04-26 06:00                             |
|                                                          |
| [ Save schedule ]                                        |
|                                                          |
| ---- Last checks ----------------------------------------|
| #42 — 2026-04-24 06:00 — COMPLETED                      |
|   3 new events, 1 new version                            |
| #41 — 2026-04-22 06:01 — COMPLETED                      |
|   0 new events, 0 new versions                           |
+----------------------------------------------------------+
```

#### UI details

- **Toggle**: HTML checkbox styled as a toggle switch via Tailwind. When off, the frequency/time controls are visually dimmed but still visible.
- **Frequency dropdown**: `<select>` with options "Every 4 hours", "Daily", "Every 2 days", "Weekly", "Monthly".
- **Time input**: `<input type="time">` (native browser time picker). Hidden when frequency is "Every 4 hours" (since interval-based runs don't use a fixed time).
- **Current server time**: Rendered server-side using `datetime.now(ZoneInfo(config.ui.timezone))` and displayed as `HH:MM (timezone_name)`.
- **Next check**: Queried from `scheduler_manager.next_run_time()`. Shows "Paused" when disabled.
- **Last 2 runs**: Queried from `PipelineRun` table, ordered by `started_at DESC`, limit 2. Each row shows run_id, timestamp, status, events_created, versions_created.

#### Routes

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/settings/save-schedule` | Save frequency/time/enabled to DB, call `scheduler_manager.apply_schedule()` or `.pause()` |

The GET `/settings` route is extended to pass `scheduler_*` settings, `next_run_time`, `server_time`, and `last_2_runs` to the template.

### Concurrency safety

1. **No parallel source execution**: `PipelineRunner.run_once` iterates sources in a `for` loop.
2. **No overlapping runs**: The callback checks `PipelineProgress` status before starting. APScheduler's `max_instances=1` is a second guard.
3. **DB locking**: `busy_timeout=10000` means SQLite waits up to 10 seconds for a write lock. The web server reads are not blocked by the pipeline writer (SQLite WAL mode is not required — the default journal mode with busy_timeout is sufficient for this single-user tool).
4. **Thread safety**: `PipelineProgress` uses `RLock` for thread-safe snapshots. The scheduler runs in a background thread (APScheduler's default `BackgroundScheduler` behavior).

### Shared pipeline helper

Extract the common pipeline execution logic from `actions.py::_run_pipeline_in_background` into `regwatch/pipeline/run_helpers.py`:

```python
def run_pipeline_background(
    *,
    session_factory,
    config,
    llm_client,
    progress: PipelineProgress,
) -> None:
    """Run all enabled sources. Used by both manual trigger and scheduler."""
```

Both `actions.py` (manual button) and the scheduler callback import and call this function.

## Files changed

| File | Change |
|------|--------|
| `regwatch/scheduler/jobs.py` | Replace with `SchedulerManager` class |
| `regwatch/main.py` | Wire real scheduler on startup, store `scheduler_manager` on `app.state` |
| `regwatch/pipeline/run_helpers.py` | **New** — shared pipeline execution helper |
| `regwatch/web/routes/actions.py` | Delegate to `run_helpers.run_pipeline_background` |
| `regwatch/web/routes/settings.py` | Add `save-schedule` route, extend `settings_view` context |
| `regwatch/web/templates/settings.html` | Add "Scheduled Updates" section |
| `tests/unit/test_scheduler.py` | **New** — unit tests for `SchedulerManager` |
| `tests/integration/test_schedule_settings.py` | **New** — integration tests for save/load/reschedule |

## Out of scope

- Per-source scheduling (user explicitly chose single global schedule).
- Running when the web server is not running (Windows Task Scheduler, etc.).
- Email/push notifications on schedule completion (can be added later).
- CSSF discovery on a schedule (separate from pipeline; can be added later).
