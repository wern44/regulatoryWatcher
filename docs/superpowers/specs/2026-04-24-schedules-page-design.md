# Schedules Page — Design Spec

**Date:** 2026-04-24
**Status:** Approved

## Goal

Consolidate all scheduled processes into a dedicated `/settings/schedules` page with an overview table, per-process edit cards, process descriptions, and mutual exclusion guards. Remove the scattered schedule sections from the main Settings page. Add two new schedulable processes: CSSF Discovery (incremental) and Catalog Refresh & Analysis.

## Current state

The Settings page currently has two schedule sections ("Scheduled Updates" for the pipeline, "Scheduled Reconciliation" for full CSSF reconciliation) embedded among other settings. The `SchedulerManager` supports two jobs. Two more processes exist but are manual-only: CSSF Discovery (incremental, from the Catalog page) and Catalog Refresh & Analysis (synchronous "Refresh catalog" button on the Catalog page).

## Architecture

### 4 scheduled processes

| # | Process | DB key prefix | Default frequency | Default time | What it does |
|---|---------|--------------|-------------------|-------------|-------------|
| 1 | **Pipeline Run** | `scheduler_` | `2days` | `06:00` | Checks RSS/SPARQL sources for new publications, creates inbox events |
| 2 | **CSSF Discovery** | `discovery_` | `weekly` | `05:30` | Incremental scrape of CSSF site for new regulations (adds to catalog) |
| 3 | **Full Reconciliation** | `reconciliation_` | `weekly` | `05:00` | Full CSSF crawl + auto-retire of removed regulations |
| 4 | **Catalog Refresh & Analysis** | `analysis_` | `monthly` | `04:00` | LLM classification of regulations + discover missing regulations. Default: **paused** (LLM-intensive) |

Each process has 3 settings in the `Setting` table:
- `{prefix}enabled` — `"true"` / `"false"`
- `{prefix}frequency` — `"4h"` / `"daily"` / `"2days"` / `"weekly"` / `"monthly"`
- `{prefix}time` — `"HH:MM"`

### SchedulerManager — extend to 4 jobs

Add two new job IDs to `SchedulerManager`:
- `DISCOVERY_JOB_ID = "scheduled_discovery"` — runs incremental CSSF discovery
- `ANALYSIS_JOB_ID = "scheduled_analysis"` — runs catalog refresh + analysis

The constructor changes from `pipeline_fn + reconciliation_fn` to accepting a `dict[str, Callable]` of job callbacks keyed by job ID. This avoids adding a new named parameter every time a job is added.

```python
class SchedulerManager:
    PIPELINE_JOB_ID = "scheduled_pipeline_run"
    DISCOVERY_JOB_ID = "scheduled_discovery"
    RECONCILIATION_JOB_ID = "scheduled_reconciliation"
    ANALYSIS_JOB_ID = "scheduled_analysis"

    def __init__(self, *, scheduler, jobs: dict[str, Callable[[], None]]):
        ...
```

All existing methods (`apply_schedule`, `pause`, `resume`, `next_run_time`, `is_running`) already take `job_id` — no signature changes needed.

### Callbacks for new jobs

**CSSF Discovery callback** (in `main.py` lifespan):
```python
def _scheduled_discovery():
    # Skip if pipeline or reconciliation is running
    service = CssfDiscoveryService(session_factory=..., config=...)
    service.run(entity_types=..., mode="incremental", triggered_by="SCHEDULER")
```

**Catalog Refresh & Analysis callback** (in `main.py` lifespan):
```python
def _scheduled_analysis():
    # Skip if any other process is running
    with session_factory() as session:
        svc = DiscoveryService(session, llm=llm_client)
        svc.classify_catalog()
        svc.discover_missing(auth_types)
        session.commit()
```

### Mutual exclusion

All 4 callbacks check a shared lock before starting. Since all processes write to the same SQLite database, only one can run at a time:
- Pipeline checks `pipeline_progress.status`
- All others check `pipeline_progress.status` AND `cssf_discovery_progress.status`
- The analysis callback also checks both progress objects

If any process is running, the scheduled tick is skipped with a log message. This reuses the existing overlap guard pattern.

### Dedicated page: `/settings/schedules`

**URL:** `GET /settings/schedules`
**Sidebar:** Add "Schedules" link under Settings (as a sub-link, same pattern as "Extraction Fields")

#### Layout (top to bottom)

**1. Header** — "Scheduled Processes" title + current server time

**2. Overview table** — one row per process showing:
- Process name + one-line description
- Status badge (Active green / Paused amber)
- Frequency as text (e.g. "Every 2 days at 06:00")
- Next run datetime (or "—" if paused)
- Last run datetime
- Last result with status colour + counts

**3. Edit cards** — 2x2 grid, one card per process:
- Toggle switch (enable/disable)
- Frequency dropdown (same `FREQUENCY_OPTIONS`)
- Time input (hidden for "4h")
- Save button
- Each card POSTs to `POST /settings/schedules/save` with a `job` hidden field identifying which process

**4. Process descriptions** — "How these processes work together" section with numbered explanations

#### Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/settings/schedules` | Render the schedules page |
| POST | `/settings/schedules/save` | Save settings for one process (accepts `job`, `enabled`, `frequency`, `time` form fields) |

The `save` route is a single handler for all 4 processes. The `job` field identifies which process: `pipeline`, `discovery`, `reconciliation`, `analysis`. The handler maps this to the correct DB key prefix and `SchedulerManager` job ID.

#### Data for the template

The `GET /settings/schedules` route gathers:
- For each of the 4 jobs: enabled, frequency, time, next_run_time from `SchedulerManager`
- Last run result: `PipelineRun` for pipeline, `DiscoveryRun` for discovery/reconciliation (filter by `triggered_by` or `mode`), and a new simple flag/timestamp for analysis
- Server time + timezone
- `FREQUENCY_OPTIONS` for dropdowns

### Settings page cleanup

Remove the "Scheduled Updates" and "Scheduled Reconciliation" sections from `settings.html`. Replace with a link card:

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

### Sidebar update

Add "Schedules" sub-link in `sidebar.html` under Settings:

```html
<a href="/settings/schedules" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">Schedules</a>
```

## Files changed

| File | Change |
|------|--------|
| `regwatch/scheduler/jobs.py` | Change constructor to `jobs: dict`, add `DISCOVERY_JOB_ID`, `ANALYSIS_JOB_ID` |
| `regwatch/main.py` | Add discovery + analysis callbacks, update constructor call, read 4 sets of settings |
| `regwatch/web/routes/settings.py` | Remove schedule-specific context from `settings_view`, remove `save_schedule` and `save_reconciliation_schedule` routes |
| `regwatch/web/routes/schedules.py` | **New** — `GET /settings/schedules` + `POST /settings/schedules/save` |
| `regwatch/web/templates/settings.html` | Remove schedule sections, add link card |
| `regwatch/web/templates/settings/schedules.html` | **New** — the dedicated schedules page |
| `regwatch/web/templates/partials/sidebar.html` | Add "Schedules" sub-link |
| `regwatch/main.py` | Register `schedules.router` |
| Tests | Update integration tests for new routes, remove old schedule route tests |

## Out of scope

- Execution ordering (e.g. "always run discovery before pipeline") — processes are independent, the overlap guard is sufficient
- Run history page per process (existing `/discovery/runs` and pipeline run list in Settings cover this)
- Manual "Run now" buttons on the schedules page (those stay on Dashboard and Catalog pages)
