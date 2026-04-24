# Pipeline Fixes, Selective Runs, Error Display & Scheduled Reconciliation — Design Spec

**Date:** 2026-04-24
**Status:** Approved

## Goal

Fix 5 failing pipeline sources, allow per-source-group pipeline runs from the Dashboard, improve error visibility in pipeline run history, and add scheduled CSSF reconciliation as an independent background job.

## Constraints

- Follow existing patterns: httpx clients, APScheduler, Setting KV table, Tailwind/HTMX UI.
- Source grouping is a display/UX concern — the pipeline runner itself doesn't change.
- Reconciliation schedule is independent of the pipeline schedule — separate job, separate settings, separate failure handling.
- Partial results from successful sources must always be committed, even when other sources fail.

## 1. Fix source failures — User-Agent headers

### Problem

5 sources fail on remote servers because EU websites block requests without a `User-Agent` header. The Legilux SPARQL sources also lack timeouts on `SPARQLWrapper`.

### Fix

Add a shared constant `USER_AGENT = "RegulatoryWatcher/1.0"` to `regwatch/pipeline/fetch/base.py` (where `REGISTRY` and `@register_source` already live). All sources import from there.

**httpx-based sources** (esma_rss, eba_rss, ec_fisma_rss): Add `headers={"User-Agent": USER_AGENT}` to the `httpx.Client()` constructor.

**SPARQLWrapper-based sources** (legilux_sparql, legilux_parliamentary): Call `wrapper.addCustomHttpHeader("User-Agent", USER_AGENT)` and set `wrapper.setTimeout(30)` after construction.

**Already-working sources** (cssf_rss, cssf_consultation): Also add the User-Agent for consistency — no behaviour change expected.

## 2. Per-source-group pipeline runs

### Source groups

Defined as a dict in `regwatch/pipeline/sources.py`:

```python
SOURCE_GROUPS: dict[str, list[str]] = {
    "cssf": ["cssf_rss", "cssf_consultation"],
    "eu_legislation": ["eur_lex_adopted", "eur_lex_proposal"],
    "luxembourg": ["legilux_sparql", "legilux_parliamentary"],
    "eu_agencies": ["esma_rss", "eba_rss", "ec_fisma_rss"],
}

SOURCE_GROUP_LABELS: dict[str, str] = {
    "cssf": "CSSF",
    "eu_legislation": "EU Legislation",
    "luxembourg": "Luxembourg",
    "eu_agencies": "EU Agencies",
}
```

### Changes to `build_enabled_sources()`

The existing `only: str | None` parameter (single source name) is replaced with `only: str | list[str] | None` to accept either a single source name or a list of source names. When a list is passed, only those sources are instantiated.

### Changes to `run_pipeline_background()`

Add an optional `source_names: list[str] | None = None` parameter. When set, pass it through to `build_enabled_sources(config, only=source_names)`.

### Changes to `POST /run-pipeline`

Accept an optional `group` form field. If `group` is set and exists in `SOURCE_GROUPS`, resolve it to a list of source names. Pass to `run_pipeline_background(source_names=...)`.

### Dashboard UI

Replace the single "Run pipeline now" button with a split button:

```
[ Run pipeline now  v ]
```

Clicking the main button runs all sources (current behaviour). Clicking the dropdown arrow shows:
- All sources
- CSSF
- EU Legislation
- Luxembourg
- EU Agencies

Each option submits `POST /run-pipeline` with a hidden `group` field. Implementation uses Alpine.js `x-data` for the dropdown state (matching existing UI patterns in the codebase).

The progress widget is unchanged — it already shows which source is being processed.

## 3. Better error display in pipeline run history

### Runner status improvement

In `regwatch/pipeline/runner.py`, line 103-104 currently:

```python
run.finished_at = datetime.now(UTC)
run.status = "COMPLETED"
```

Change to:

```python
run.finished_at = datetime.now(UTC)
run.status = "COMPLETED_WITH_ERRORS" if run.sources_failed else "COMPLETED"
```

### Settings template — "Recent pipeline runs" section

Currently shows: `#42 — COMPLETED — 3 events`

Change to show:

```
#42 — COMPLETED — 3 events, 1 version — 2026-04-24 06:00
#41 — COMPLETED_WITH_ERRORS — 5 events, 0 versions — 2026-04-22 06:01
     Failed: legilux_sparql, esma_rss
#40 — FAILED — 0 events — 2026-04-20 06:00
     Error: ConnectionError: ...
```

Status colour coding:
- `COMPLETED` → green
- `COMPLETED_WITH_ERRORS` → amber
- `FAILED` / `ABORTED` → red
- `RUNNING` → blue

Show `sources_failed` when non-empty. Show `error` when present. Show both `events_created` and `versions_created`.

### Settings template — "Last checks" section (Scheduled Updates card)

Same improvements as above. The template already shows status and counts — add failed sources and error display.

## 4. Scheduled CSSF reconciliation

### Database settings

Three new keys in the `Setting` table:

| Key | Default | Options |
|---|---|---|
| `reconciliation_enabled` | `"true"` | `"true"` / `"false"` |
| `reconciliation_frequency` | `"weekly"` | same 5 as pipeline: `"4h"`, `"daily"`, `"2days"`, `"weekly"`, `"monthly"` |
| `reconciliation_time` | `"05:00"` | `"HH:MM"` |

### SchedulerManager — multi-job support

The current `SchedulerManager` manages a single job with a hardcoded `JOB_ID`. Extend it to support multiple named jobs:

```python
class SchedulerManager:
    PIPELINE_JOB_ID = "scheduled_pipeline_run"
    RECONCILIATION_JOB_ID = "scheduled_reconciliation"

    def __init__(self, *, scheduler, pipeline_fn, reconciliation_fn):
        ...

    def apply_schedule(self, job_id: str, frequency: str, time_str: str) -> None:
        ...

    def pause(self, job_id: str) -> None:
        ...

    def resume(self, job_id: str) -> None:
        ...

    def next_run_time(self, job_id: str) -> datetime | None:
        ...

    def is_running(self, job_id: str) -> bool:
        ...
```

The old single-argument methods (`apply_schedule(freq, time)`) are replaced with `apply_schedule(job_id, freq, time)`. All callers (main.py lifespan, settings routes) are updated.

### Reconciliation callback

The reconciliation callback in `main.py` lifespan:

1. Check if reconciliation is already running (same overlap guard pattern as pipeline).
2. Call `CssfDiscoveryService.run(entity_types=[AIFM, CHAPTER15_MANCO], mode="full", triggered_by="scheduler")`.
3. Error handling: log and continue — reconciliation failure must not block anything.

### Settings UI

A new "Scheduled Reconciliation" section on `/settings`, placed after the "Scheduled Updates" section. Same layout: toggle, frequency dropdown, time input, server time display, next run time, last 2 discovery runs.

### Routes

| Method | Path | Purpose |
|---|---|---|
| POST | `/settings/save-reconciliation-schedule` | Save reconciliation schedule settings |

The `settings_view` route is extended to pass reconciliation settings, next run time, and last 2 `DiscoveryRun` rows.

### Startup flow

In `main.py` lifespan, after the pipeline scheduler setup:

1. Read `reconciliation_enabled`, `reconciliation_frequency`, `reconciliation_time` from DB.
2. Call `scheduler_manager.apply_schedule(RECONCILIATION_JOB_ID, freq, time)`.
3. If disabled, pause the job.

## 5. Partial results already survive (no change needed)

The pipeline runner already:
- Catches per-document exceptions (runner.py:95) and continues to the next document.
- Catches per-source exceptions (runner.py:97) and continues to the next source.
- The final `session.commit()` in `run_helpers.py` commits everything that succeeded.

The only gap was **visibility** — fixed in section 3 by distinguishing `COMPLETED` from `COMPLETED_WITH_ERRORS` and showing failed sources in the UI.

## Files changed

| File | Change |
|------|--------|
| `regwatch/pipeline/fetch/base.py` | Add `USER_AGENT` constant |
| `regwatch/pipeline/fetch/esma_rss.py` | Add User-Agent header |
| `regwatch/pipeline/fetch/eba_rss.py` | Add User-Agent header |
| `regwatch/pipeline/fetch/ec_fisma_rss.py` | Add User-Agent header |
| `regwatch/pipeline/fetch/legilux_sparql.py` | Add User-Agent + timeout |
| `regwatch/pipeline/fetch/legilux_parliamentary.py` | Add User-Agent + timeout |
| `regwatch/pipeline/fetch/cssf_rss.py` | Add User-Agent for consistency |
| `regwatch/pipeline/fetch/cssf_consultation.py` | Add User-Agent for consistency |
| `regwatch/pipeline/sources.py` | Add `SOURCE_GROUPS`, `SOURCE_GROUP_LABELS`; extend `build_enabled_sources` |
| `regwatch/pipeline/run_helpers.py` | Add `source_names` parameter |
| `regwatch/pipeline/runner.py` | Set `COMPLETED_WITH_ERRORS` status |
| `regwatch/scheduler/jobs.py` | Multi-job `SchedulerManager` |
| `regwatch/main.py` | Wire reconciliation job, update pipeline job calls |
| `regwatch/web/routes/actions.py` | Accept `group` form field |
| `regwatch/web/routes/settings.py` | Add reconciliation schedule route, extend settings_view |
| `regwatch/web/templates/dashboard.html` | Split button with group dropdown |
| `regwatch/web/templates/settings.html` | Better run history display + reconciliation schedule section |
| Tests | New/updated unit + integration tests |

## Out of scope

- Per-individual-source buttons (only groups)
- Retry logic for failed sources within the same run
- Email/push notifications on failure
- Reconciliation progress display during scheduled runs (runs silently in background)
