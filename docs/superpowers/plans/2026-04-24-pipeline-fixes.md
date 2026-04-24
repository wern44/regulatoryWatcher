# Pipeline Fixes, Selective Runs, Error Display & Scheduled Reconciliation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 failing pipeline sources, add per-source-group pipeline runs from the Dashboard, improve error visibility in run history, and add scheduled CSSF reconciliation as an independent background job.

**Architecture:** Add `USER_AGENT` to all fetch sources, define source groups in `sources.py`, extend `run_pipeline_background` and `POST /run-pipeline` for group filtering, set `COMPLETED_WITH_ERRORS` in the runner, extend `SchedulerManager` for multi-job support, wire reconciliation into the lifespan, and add UI sections for all of the above.

**Tech Stack:** httpx, SPARQLWrapper, APScheduler 3.x, FastAPI, SQLAlchemy, Jinja2/HTMX/Tailwind/Alpine.js, pytest

---

### Task 1: Add User-Agent to all fetch sources

**Files:**
- Modify: `regwatch/pipeline/fetch/base.py`
- Modify: `regwatch/pipeline/fetch/esma_rss.py`
- Modify: `regwatch/pipeline/fetch/eba_rss.py`
- Modify: `regwatch/pipeline/fetch/ec_fisma_rss.py`
- Modify: `regwatch/pipeline/fetch/legilux_sparql.py`
- Modify: `regwatch/pipeline/fetch/legilux_parliamentary.py`
- Modify: `regwatch/pipeline/fetch/cssf_rss.py`
- Modify: `regwatch/pipeline/fetch/cssf_consultation.py`
- Modify: `regwatch/pipeline/fetch/eur_lex_adopted.py`
- Modify: `regwatch/pipeline/fetch/eur_lex_proposal.py`

- [ ] **Step 1: Add `USER_AGENT` constant to `base.py`**

In `regwatch/pipeline/fetch/base.py`, add after line 8 (`from regwatch.domain.types import RawDocument`):

```python
USER_AGENT = "RegulatoryWatcher/1.0"
```

- [ ] **Step 2: Add User-Agent to `esma_rss.py`**

Change import line to also import `USER_AGENT`:

```python
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
```

Change the httpx.Client line (line 23):

```python
# Old:
with httpx.Client(timeout=30.0, follow_redirects=True) as client:
# New:
with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
```

- [ ] **Step 3: Add User-Agent to `eba_rss.py`**

Same pattern as esma_rss.py. Change import:

```python
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
```

Change httpx.Client line (line 23):

```python
with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
```

- [ ] **Step 4: Add User-Agent to `ec_fisma_rss.py`**

Change import:

```python
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
```

Change httpx.Client line (line 36):

```python
with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
```

- [ ] **Step 5: Add User-Agent and timeout to `legilux_sparql.py`**

Change import:

```python
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
```

Change `_run_query` method (lines 57-61):

```python
    def _run_query(self, query: str) -> dict[str, Any]:
        wrapper = SPARQLWrapper(ENDPOINT)
        wrapper.addCustomHttpHeader("User-Agent", USER_AGENT)
        wrapper.setTimeout(30)
        wrapper.setQuery(query)
        wrapper.setReturnFormat(JSON)
        return wrapper.queryAndConvert()  # type: ignore[return-value]
```

- [ ] **Step 6: Add User-Agent and timeout to `legilux_parliamentary.py`**

Change import:

```python
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
```

Change `_run_query` method (lines 61-65):

```python
    def _run_query(self, query: str) -> dict[str, Any]:
        wrapper = SPARQLWrapper(ENDPOINT)
        wrapper.addCustomHttpHeader("User-Agent", USER_AGENT)
        wrapper.setTimeout(30)
        wrapper.setQuery(query)
        wrapper.setReturnFormat(JSON)
        return wrapper.queryAndConvert()  # type: ignore[return-value]
```

- [ ] **Step 7: Add User-Agent to `cssf_rss.py`**

Change import:

```python
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
```

Change httpx.Client line (line 23):

```python
self._client = httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
```

- [ ] **Step 8: Add User-Agent to `cssf_consultation.py`**

Change import:

```python
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
```

Change httpx.Client line (line 34):

```python
with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
```

- [ ] **Step 9: Add User-Agent and timeout to `eur_lex_adopted.py`**

Change import:

```python
from regwatch.pipeline.fetch.base import USER_AGENT, register_source
```

Change `_run_query` method:

```python
    def _run_query(self, query: str) -> dict[str, Any]:
        wrapper = SPARQLWrapper(ENDPOINT)
        wrapper.addCustomHttpHeader("User-Agent", USER_AGENT)
        wrapper.setTimeout(30)
        wrapper.setQuery(query)
        wrapper.setReturnFormat(JSON)
        return wrapper.queryAndConvert()  # type: ignore[return-value]
```

- [ ] **Step 10: Add User-Agent and timeout to `eur_lex_proposal.py`**

Read the file first — it has the same `_run_query` pattern. Apply the same change as `eur_lex_adopted.py`:

Change import to include `USER_AGENT`, and update `_run_query` to add `wrapper.addCustomHttpHeader("User-Agent", USER_AGENT)` and `wrapper.setTimeout(30)`.

- [ ] **Step 11: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All PASS (User-Agent changes don't affect test behaviour since tests mock HTTP)

- [ ] **Step 12: Commit**

```bash
git add regwatch/pipeline/fetch/
git commit -m "fix(fetch): add User-Agent header and timeouts to all sources

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add source groups and extend `build_enabled_sources`

**Files:**
- Modify: `regwatch/pipeline/sources.py`
- Test: `tests/unit/test_sources.py`

- [ ] **Step 1: Write tests for source groups and list-based filtering**

Create or extend `tests/unit/test_sources.py`:

```python
# tests/unit/test_sources.py
from regwatch.pipeline.sources import SOURCE_GROUP_LABELS, SOURCE_GROUPS


def test_source_groups_cover_all_known_sources():
    all_grouped = set()
    for names in SOURCE_GROUPS.values():
        all_grouped.update(names)
    expected = {
        "cssf_rss", "cssf_consultation",
        "eur_lex_adopted", "eur_lex_proposal",
        "legilux_sparql", "legilux_parliamentary",
        "esma_rss", "eba_rss", "ec_fisma_rss",
    }
    assert expected == all_grouped


def test_source_group_labels_match_groups():
    assert set(SOURCE_GROUP_LABELS.keys()) == set(SOURCE_GROUPS.keys())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_sources.py -v`
Expected: FAIL — `SOURCE_GROUPS` and `SOURCE_GROUP_LABELS` don't exist.

- [ ] **Step 3: Add `SOURCE_GROUPS` and `SOURCE_GROUP_LABELS` to `sources.py`**

Add at the top of `regwatch/pipeline/sources.py`, after the existing imports:

```python
# Logical groupings of sources for selective pipeline runs.
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

- [ ] **Step 4: Extend `build_enabled_sources` to accept a list**

Change the `only` parameter type and filtering logic in `regwatch/pipeline/sources.py`:

```python
def build_enabled_sources(
    config: AppConfig, *, only: str | list[str] | None = None
) -> list[Any]:
    """Instantiate every enabled source in the config.

    If `only` is a string, restrict to that single source name.
    If `only` is a list, restrict to those source names.
    """
    import_all_sources()
    only_set: set[str] | None = None
    if isinstance(only, str):
        only_set = {only}
    elif isinstance(only, list):
        only_set = set(only)

    instances: list[Any] = []
    for name, source_cfg in config.sources.items():
        if not source_cfg.enabled:
            continue
        if only_set is not None and name not in only_set:
            continue
        instances.append(instantiate_source(name, source_cfg))
    return instances
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_sources.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/sources.py tests/unit/test_sources.py
git commit -m "feat(pipeline): add source groups and list-based filtering

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Extend `run_pipeline_background` and `POST /run-pipeline` for group filtering

**Files:**
- Modify: `regwatch/pipeline/run_helpers.py`
- Modify: `regwatch/web/routes/actions.py`
- Modify: `regwatch/web/templates/dashboard.html`
- Test: `tests/unit/test_run_helpers.py`

- [ ] **Step 1: Add test for `source_names` parameter in `run_helpers.py`**

Add to `tests/unit/test_run_helpers.py`:

```python
def test_passes_source_names_to_build_enabled_sources():
    progress = PipelineProgress()
    mock_session = MagicMock()
    mock_session_factory = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)))

    with patch(
        "regwatch.pipeline.run_helpers.build_enabled_sources",
        return_value=[],
    ) as mock_build, patch(
        "regwatch.pipeline.run_helpers.build_runner",
    ) as mock_runner:
        mock_runner.return_value.run_once.return_value = 1
        run_pipeline_background(
            session_factory=mock_session_factory,
            config=MagicMock(),
            llm_client=MagicMock(),
            progress=progress,
            source_names=["cssf_rss", "cssf_consultation"],
        )
    mock_build.assert_called_once()
    call_kwargs = mock_build.call_args
    assert call_kwargs.kwargs.get("only") == ["cssf_rss", "cssf_consultation"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_run_helpers.py::test_passes_source_names_to_build_enabled_sources -v`
Expected: FAIL — `run_pipeline_background` doesn't accept `source_names`.

- [ ] **Step 3: Add `source_names` parameter to `run_pipeline_background`**

In `regwatch/pipeline/run_helpers.py`, update the function signature and the call to `build_enabled_sources`:

```python
def run_pipeline_background(
    *,
    session_factory,
    config,
    llm_client,
    progress: PipelineProgress,
    source_names: list[str] | None = None,
) -> None:
    """Run all enabled sources in a fresh DB session.

    Used by both the manual "Run pipeline now" button and the scheduler.
    Catches all exceptions and reports them via *progress*.
    If *source_names* is set, only those sources are run.
    """
    try:
        sources = build_enabled_sources(config, only=source_names)
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

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_run_helpers.py -v`
Expected: All PASS

- [ ] **Step 5: Update `POST /run-pipeline` to accept a `group` form field**

In `regwatch/web/routes/actions.py`, add imports and update the route:

```python
from fastapi import APIRouter, Form, Request

from regwatch.pipeline.sources import SOURCE_GROUPS
```

Update the `run_pipeline` function signature to accept `group`:

```python
@router.post("/run-pipeline", response_class=HTMLResponse)
def run_pipeline(
    request: Request,
    group: str | None = Form(None),
) -> HTMLResponse:
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

    source_names: list[str] | None = None
    if group and group in SOURCE_GROUPS:
        source_names = SOURCE_GROUPS[group]

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
            "source_names": source_names,
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
```

- [ ] **Step 6: Update `dashboard.html` with split-button dropdown**

Replace the existing button (lines 6-12) in `regwatch/web/templates/dashboard.html`:

```html
    <div x-data="{ open: false }" class="relative">
      <div class="inline-flex rounded shadow-sm">
        <button type="button"
                hx-post="/run-pipeline"
                hx-target="#pipeline-progress-slot"
                hx-swap="innerHTML"
                class="px-4 py-2 bg-slate-800 text-white rounded-l hover:bg-slate-700 text-sm font-semibold">
          Run all sources
        </button>
        <button type="button" @click="open = !open"
                class="px-2 py-2 bg-slate-800 text-white rounded-r border-l border-slate-600 hover:bg-slate-700 text-sm">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
          </svg>
        </button>
      </div>
      <div x-show="open" @click.outside="open = false" x-cloak
           class="absolute right-0 mt-1 w-48 bg-white rounded shadow-lg border z-10">
        <button type="button" @click="open = false"
                hx-post="/run-pipeline" hx-vals='{"group":"cssf"}'
                hx-target="#pipeline-progress-slot" hx-swap="innerHTML"
                class="block w-full text-left px-4 py-2 text-sm hover:bg-slate-100">CSSF</button>
        <button type="button" @click="open = false"
                hx-post="/run-pipeline" hx-vals='{"group":"eu_legislation"}'
                hx-target="#pipeline-progress-slot" hx-swap="innerHTML"
                class="block w-full text-left px-4 py-2 text-sm hover:bg-slate-100">EU Legislation</button>
        <button type="button" @click="open = false"
                hx-post="/run-pipeline" hx-vals='{"group":"luxembourg"}'
                hx-target="#pipeline-progress-slot" hx-swap="innerHTML"
                class="block w-full text-left px-4 py-2 text-sm hover:bg-slate-100">Luxembourg</button>
        <button type="button" @click="open = false"
                hx-post="/run-pipeline" hx-vals='{"group":"eu_agencies"}'
                hx-target="#pipeline-progress-slot" hx-swap="innerHTML"
                class="block w-full text-left px-4 py-2 text-sm hover:bg-slate-100">EU Agencies</button>
      </div>
    </div>
```

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add regwatch/pipeline/run_helpers.py regwatch/web/routes/actions.py regwatch/web/templates/dashboard.html tests/unit/test_run_helpers.py
git commit -m "feat(pipeline): per-source-group pipeline runs from Dashboard

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Set `COMPLETED_WITH_ERRORS` status and improve run history display

**Files:**
- Modify: `regwatch/pipeline/runner.py`
- Modify: `regwatch/web/templates/settings.html`
- Test: `tests/unit/test_pipeline_runner.py`

- [ ] **Step 1: Write test for `COMPLETED_WITH_ERRORS` status**

Add to existing `tests/unit/test_pipeline_runner.py` (or create if it doesn't exist). You need to check if the file exists first — read it to find existing test patterns.

```python
def test_run_sets_completed_with_errors_when_source_fails(tmp_path):
    """When a source raises, status should be COMPLETED_WITH_ERRORS, not COMPLETED."""
    from unittest.mock import MagicMock
    from regwatch.pipeline.runner import PipelineRunner
    from regwatch.db.models import PipelineRun, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    class FailingSource:
        name = "fail_source"
        def fetch(self, since):
            raise RuntimeError("boom")

    class OkSource:
        name = "ok_source"
        def fetch(self, since):
            return iter([])

    with Session(engine) as session:
        runner = PipelineRunner(
            session,
            sources=[OkSource(), FailingSource()],
            extract=MagicMock(),
            match=MagicMock(),
        )
        run_id = runner.run_once()
        session.commit()
        run = session.get(PipelineRun, run_id)
        assert run.status == "COMPLETED_WITH_ERRORS"
        assert "fail_source" in run.sources_failed


def test_run_sets_completed_when_all_sources_ok(tmp_path):
    from unittest.mock import MagicMock
    from regwatch.pipeline.runner import PipelineRunner
    from regwatch.db.models import PipelineRun, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    class OkSource:
        name = "ok_source"
        def fetch(self, since):
            return iter([])

    with Session(engine) as session:
        runner = PipelineRunner(
            session,
            sources=[OkSource()],
            extract=MagicMock(),
            match=MagicMock(),
        )
        run_id = runner.run_once()
        session.commit()
        run = session.get(PipelineRun, run_id)
        assert run.status == "COMPLETED"
        assert run.sources_failed == []
```

- [ ] **Step 2: Run the test to verify the first one fails**

Run: `pytest tests/unit/test_pipeline_runner.py::test_run_sets_completed_with_errors_when_source_fails -v`
Expected: FAIL — status is "COMPLETED" instead of "COMPLETED_WITH_ERRORS"

- [ ] **Step 3: Fix the runner status logic**

In `regwatch/pipeline/runner.py`, change lines 103-104:

```python
# Old:
        run.finished_at = datetime.now(UTC)
        run.status = "COMPLETED"
# New:
        run.finished_at = datetime.now(UTC)
        run.status = "COMPLETED_WITH_ERRORS" if run.sources_failed else "COMPLETED"
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/unit/test_pipeline_runner.py -v`
Expected: All PASS

- [ ] **Step 5: Improve "Recent pipeline runs" in `settings.html`**

Replace the "Recent pipeline runs" section at the bottom of `regwatch/web/templates/settings.html` (the `<section>` starting with `<h2 ... >Recent pipeline runs</h2>`):

```html
  <section class="bg-white p-4 rounded shadow-sm border">
    <h2 class="text-lg font-semibold mb-2">Recent pipeline runs</h2>
    {% if runs %}
    <ul class="divide-y text-sm">
      {% for r in runs %}
      <li class="py-2">
        <div class="flex justify-between">
          <span>
            <span class="font-medium">#{{ r.run_id }}</span>
            <span class="{% if r.status == 'COMPLETED' %}text-green-700{% elif r.status == 'COMPLETED_WITH_ERRORS' %}text-amber-600{% elif r.status == 'RUNNING' %}text-blue-600{% else %}text-red-700{% endif %} font-medium">
              {{ r.status }}
            </span>
            — {{ r.events_created }} event{{ 's' if r.events_created != 1 else '' }}, {{ r.versions_created }} version{{ 's' if r.versions_created != 1 else '' }}
          </span>
          <span class="text-xs text-slate-500">{{ r.started_at.strftime('%Y-%m-%d %H:%M') }}</span>
        </div>
        {% if r.sources_failed %}
        <div class="text-xs text-amber-700 mt-1">
          Failed sources: {{ r.sources_failed | join(', ') }}
        </div>
        {% endif %}
        {% if r.error %}
        <div class="text-xs text-red-700 mt-1 truncate" title="{{ r.error }}">
          Error: {{ r.error[:120] }}
        </div>
        {% endif %}
      </li>
      {% endfor %}
    </ul>
    {% else %}
    <p class="text-sm text-slate-500">No runs yet.</p>
    {% endif %}
  </section>
```

- [ ] **Step 6: Also improve the "Last checks" section in the Scheduled Updates card**

Find the "Last checks" `<ul>` inside the Scheduled Updates section and replace it with:

```html
    {% if last_runs %}
    <div class="mt-4 pt-3 border-t">
      <h3 class="text-sm font-medium text-slate-600 mb-2">Last checks</h3>
      <ul class="space-y-2 text-sm">
        {% for r in last_runs %}
        <li>
          <div class="flex justify-between items-start">
            <div>
              <span class="font-medium">#{{ r.run_id }}</span>
              <span class="{% if r.status == 'COMPLETED' %}text-green-700{% elif r.status == 'COMPLETED_WITH_ERRORS' %}text-amber-600{% elif r.status == 'RUNNING' %}text-blue-600{% else %}text-red-700{% endif %} font-medium">
                {{ r.status }}
              </span>
              <span class="text-slate-500 text-xs ml-1">{{ r.started_at.strftime('%Y-%m-%d %H:%M') }}</span>
            </div>
            <div class="text-xs text-slate-500 text-right">
              {{ r.events_created }} event{{ 's' if r.events_created != 1 else '' }},
              {{ r.versions_created }} version{{ 's' if r.versions_created != 1 else '' }}
            </div>
          </div>
          {% if r.sources_failed %}
          <div class="text-xs text-amber-700 mt-1">
            Failed: {{ r.sources_failed | join(', ') }}
          </div>
          {% endif %}
        </li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}
```

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add regwatch/pipeline/runner.py regwatch/web/templates/settings.html tests/unit/test_pipeline_runner.py
git commit -m "feat(pipeline): COMPLETED_WITH_ERRORS status and improved run history display

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Extend SchedulerManager for multi-job support

**Files:**
- Modify: `regwatch/scheduler/jobs.py`
- Modify: `tests/unit/test_scheduler_jobs.py`

- [ ] **Step 1: Write tests for multi-job SchedulerManager**

Replace `tests/unit/test_scheduler_jobs.py` with:

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
        pipeline_fn=MagicMock(),
        reconciliation_fn=MagicMock(),
    )
    scheduler.start()
    yield mgr
    if scheduler.running:
        scheduler.shutdown(wait=False)


def test_apply_schedule_creates_pipeline_job(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert job is not None


def test_apply_schedule_creates_reconciliation_job(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.RECONCILIATION_JOB_ID, "weekly", "05:00")
    job = manager._scheduler.get_job(SchedulerManager.RECONCILIATION_JOB_ID)
    assert job is not None


def test_two_jobs_coexist(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.apply_schedule(SchedulerManager.RECONCILIATION_JOB_ID, "weekly", "05:00")
    jobs = manager._scheduler.get_jobs()
    assert len(jobs) == 2
    job_ids = {j.id for j in jobs}
    assert SchedulerManager.PIPELINE_JOB_ID in job_ids
    assert SchedulerManager.RECONCILIATION_JOB_ID in job_ids


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
    manager.apply_schedule(SchedulerManager.RECONCILIATION_JOB_ID, "weekly", "05:00")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None
    assert manager.next_run_time(SchedulerManager.RECONCILIATION_JOB_ID) is not None


def test_next_run_time_returns_none_when_no_job(manager: SchedulerManager):
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None


def test_next_run_time_returns_datetime_when_scheduled(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    nrt = manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID)
    assert isinstance(nrt, datetime)


def test_4h_frequency_uses_interval_trigger(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "4h", "00:00")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert "interval" in str(type(job.trigger)).lower()


def test_daily_frequency_uses_cron_trigger(manager: SchedulerManager):
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

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/unit/test_scheduler_jobs.py -v`
Expected: FAIL — constructor signature mismatch (`pipeline_fn` / `reconciliation_fn` don't exist yet).

- [ ] **Step 3: Update `SchedulerManager` for multi-job support**

Replace `regwatch/scheduler/jobs.py`:

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
    """Manages named scheduled jobs (pipeline + reconciliation)."""

    PIPELINE_JOB_ID = "scheduled_pipeline_run"
    RECONCILIATION_JOB_ID = "scheduled_reconciliation"

    def __init__(
        self,
        *,
        scheduler: BackgroundScheduler,
        pipeline_fn: Callable[[], None],
        reconciliation_fn: Callable[[], None],
    ) -> None:
        self._scheduler = scheduler
        self._fns: dict[str, Callable[[], None]] = {
            self.PIPELINE_JOB_ID: pipeline_fn,
            self.RECONCILIATION_JOB_ID: reconciliation_fn,
        }
        self._timezone: str = str(scheduler.timezone)

    def apply_schedule(
        self, job_id: str, frequency: str, time_str: str
    ) -> None:
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
        logger.info(
            "Scheduled %s: frequency=%s, time=%s", job_id, frequency, time_str
        )

    def pause(self, job_id: str) -> None:
        """Pause a scheduled job (it stays registered but won't fire)."""
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.pause_job(job_id)
            logger.info("Job %s paused", job_id)

    def resume(self, job_id: str) -> None:
        """Resume a paused job."""
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.resume_job(job_id)
            logger.info("Job %s resumed", job_id)

    def next_run_time(self, job_id: str) -> datetime | None:
        """Return the next fire time, or None if paused/no job."""
        job = self._scheduler.get_job(job_id)
        if job is None:
            return None
        return job.next_run_time

    def is_running(self, job_id: str) -> bool:
        """True if the job is registered and active (not paused)."""
        job = self._scheduler.get_job(job_id)
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
git commit -m "feat(scheduler): extend SchedulerManager for multi-job support

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Wire reconciliation job and update pipeline job calls in `main.py`

**Files:**
- Modify: `regwatch/main.py`
- Modify: `regwatch/web/routes/settings.py`

- [ ] **Step 1: Update `main.py` lifespan for multi-job SchedulerManager**

In `regwatch/main.py`, the lifespan needs two changes:
1. The `SchedulerManager` constructor now takes `pipeline_fn` and `reconciliation_fn` instead of `run_fn`.
2. Add a reconciliation callback and wire up its schedule from DB settings.

Replace the lifespan block with:

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from apscheduler.schedulers.background import BackgroundScheduler  # noqa: PLC0415
        from regwatch.pipeline.run_helpers import run_pipeline_background  # noqa: PLC0415
        from regwatch.services.cssf_discovery import CssfDiscoveryService  # noqa: PLC0415
        from regwatch.db.models import AuthorizationType  # noqa: PLC0415

        bg_scheduler = BackgroundScheduler(timezone=config.ui.timezone)

        pipeline_progress = PipelineProgress()

        def _scheduled_pipeline() -> None:
            snap = pipeline_progress.snapshot()
            if snap["status"] == "running":
                logger.info("Scheduled pipeline tick skipped — already running")
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

        def _scheduled_reconciliation() -> None:
            logger.info("Scheduled CSSF reconciliation starting")
            try:
                auth_types = [
                    AuthorizationType(a.type)
                    for a in config.entity.authorizations
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

        scheduler_manager = SchedulerManager(
            scheduler=bg_scheduler,
            pipeline_fn=_scheduled_pipeline,
            reconciliation_fn=_scheduled_reconciliation,
        )

        # Read schedule settings from DB.
        with session_factory() as session:
            svc = SettingsService(session)
            # Pipeline schedule
            sched_enabled = svc.get("scheduler_enabled", "true") or "true"
            sched_freq = svc.get("scheduler_frequency", "2days") or "2days"
            sched_time = svc.get("scheduler_time", "06:00") or "06:00"
            # Reconciliation schedule
            recon_enabled = svc.get("reconciliation_enabled", "true") or "true"
            recon_freq = svc.get("reconciliation_frequency", "weekly") or "weekly"
            recon_time = svc.get("reconciliation_time", "05:00") or "05:00"

        scheduler_manager.apply_schedule(
            SchedulerManager.PIPELINE_JOB_ID, sched_freq, sched_time
        )
        scheduler_manager.apply_schedule(
            SchedulerManager.RECONCILIATION_JOB_ID, recon_freq, recon_time
        )
        bg_scheduler.start()
        if sched_enabled != "true":
            scheduler_manager.pause(SchedulerManager.PIPELINE_JOB_ID)
        if recon_enabled != "true":
            scheduler_manager.pause(SchedulerManager.RECONCILIATION_JOB_ID)

        app.state.scheduler_manager = scheduler_manager
        app.state.pipeline_progress = pipeline_progress
        yield
        if bg_scheduler.running:
            bg_scheduler.shutdown(wait=False)
```

- [ ] **Step 2: Update settings routes for the new `apply_schedule` signature**

In `regwatch/web/routes/settings.py`, the `save_schedule` route calls `manager.apply_schedule(...)`. Update it to pass the job ID:

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

    from regwatch.scheduler.jobs import SchedulerManager  # noqa: PLC0415
    manager = request.app.state.scheduler_manager
    manager.apply_schedule(
        SchedulerManager.PIPELINE_JOB_ID, scheduler_frequency, scheduler_time
    )
    if enabled:
        manager.resume(SchedulerManager.PIPELINE_JOB_ID)
    else:
        manager.pause(SchedulerManager.PIPELINE_JOB_ID)

    return RedirectResponse(url="/settings", status_code=303)
```

Also update the `settings_view` to pass the pipeline job ID for next_run_time:

```python
    from regwatch.scheduler.jobs import SchedulerManager as SM  # noqa: PLC0415
    scheduler_manager = getattr(request.app.state, "scheduler_manager", None)
    next_run = scheduler_manager.next_run_time(SM.PIPELINE_JOB_ID) if scheduler_manager else None
```

- [ ] **Step 3: Run smoke tests**

Run: `pytest tests/integration/test_app_smoke.py tests/integration/test_schedule_settings.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add regwatch/main.py regwatch/web/routes/settings.py
git commit -m "feat(scheduler): wire reconciliation job into lifespan, update callers for multi-job API

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Add reconciliation schedule UI and route

**Files:**
- Modify: `regwatch/web/routes/settings.py`
- Modify: `regwatch/web/templates/settings.html`
- Test: `tests/integration/test_schedule_settings.py`

- [ ] **Step 1: Add integration test for reconciliation schedule**

Add to `tests/integration/test_schedule_settings.py`:

```python
def test_settings_page_shows_reconciliation_section(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Scheduled Reconciliation" in resp.text
    assert "reconciliation_frequency" in resp.text


def test_save_reconciliation_schedule_persists(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/settings/save-reconciliation-schedule",
        data={
            "reconciliation_enabled": "true",
            "reconciliation_frequency": "monthly",
            "reconciliation_time": "04:00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    resp2 = client.get("/settings")
    assert "monthly" in resp2.text
```

- [ ] **Step 2: Add `save-reconciliation-schedule` route**

In `regwatch/web/routes/settings.py`, add after the `save_schedule` route:

```python
@router.post("/save-reconciliation-schedule")
def save_reconciliation_schedule(
    request: Request,
    reconciliation_frequency: str = Form(...),
    reconciliation_time: str = Form("05:00"),
    reconciliation_enabled: str | None = Form(None),
) -> RedirectResponse:
    enabled = reconciliation_enabled is not None
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        svc.set("reconciliation_enabled", "true" if enabled else "false")
        svc.set("reconciliation_frequency", reconciliation_frequency)
        svc.set("reconciliation_time", reconciliation_time)
        session.commit()

    from regwatch.scheduler.jobs import SchedulerManager  # noqa: PLC0415
    manager = request.app.state.scheduler_manager
    manager.apply_schedule(
        SchedulerManager.RECONCILIATION_JOB_ID,
        reconciliation_frequency,
        reconciliation_time,
    )
    if enabled:
        manager.resume(SchedulerManager.RECONCILIATION_JOB_ID)
    else:
        manager.pause(SchedulerManager.RECONCILIATION_JOB_ID)

    return RedirectResponse(url="/settings", status_code=303)
```

- [ ] **Step 3: Extend `settings_view` with reconciliation context**

Add to the scheduler settings block in `settings_view`:

```python
        recon_enabled = svc.get("reconciliation_enabled", "true") == "true"
        recon_freq = svc.get("reconciliation_frequency", "weekly")
        recon_time = svc.get("reconciliation_time", "05:00")
```

Add `DiscoveryRun` to the imports:

```python
from regwatch.db.models import DiscoveryRun, DocumentVersion, ExtractionFieldType, PipelineRun
```

Query the last 2 discovery runs:

```python
        last_discovery_runs = (
            session.query(DiscoveryRun)
            .order_by(DiscoveryRun.started_at.desc())
            .limit(2)
            .all()
        )
```

Add reconciliation next run time:

```python
    recon_next_run = scheduler_manager.next_run_time(SM.RECONCILIATION_JOB_ID) if scheduler_manager else None
```

Add to the template context:

```python
            "recon_enabled": recon_enabled,
            "recon_freq": recon_freq,
            "recon_time": recon_time,
            "recon_next_run": recon_next_run,
            "last_discovery_runs": last_discovery_runs,
```

- [ ] **Step 4: Add "Scheduled Reconciliation" section to `settings.html`**

Insert after the "Scheduled Updates" `</section>` closing tag and before the "CSSF catalog reconciliation" section:

```html
  <section class="bg-white p-4 rounded shadow-sm border mb-4">
    <h2 class="text-lg font-semibold mb-2">Scheduled Reconciliation</h2>
    <p class="text-sm text-slate-600 mb-3">
      Periodically run the full CSSF catalog reconciliation to discover new
      regulations, update applicability, and retire removed items.
    </p>
    <form method="post" action="/settings/save-reconciliation-schedule" class="space-y-4">
      <div class="flex items-center gap-3">
        <label class="text-sm font-medium" for="recon_toggle">Enable automatic reconciliation</label>
        <label class="relative inline-flex items-center cursor-pointer">
          <input type="checkbox" id="recon_toggle" name="reconciliation_enabled" value="true"
                 {% if recon_enabled %}checked{% endif %}
                 class="sr-only peer"
                 onchange="document.getElementById('recon_controls').classList.toggle('opacity-50', !this.checked)">
          <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300
                      rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white
                      after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white
                      after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5
                      after:transition-all peer-checked:bg-blue-600"></div>
        </label>
      </div>

      <div id="recon_controls" class="space-y-3 {% if not recon_enabled %}opacity-50{% endif %}">
        <div>
          <label class="block text-xs font-medium mb-1" for="reconciliation_frequency">Reconciliation frequency</label>
          <select name="reconciliation_frequency" id="reconciliation_frequency"
                  class="w-full border rounded px-3 py-2 text-sm"
                  onchange="document.getElementById('recon_time_row').style.display = this.value === '4h' ? 'none' : 'block'">
            {% for value, label in frequency_options.items() %}
            <option value="{{ value }}" {% if value == recon_freq %}selected{% endif %}>{{ label }}</option>
            {% endfor %}
          </select>
        </div>

        <div id="recon_time_row" {% if recon_freq == '4h' %}style="display:none"{% endif %}>
          <label class="block text-xs font-medium mb-1" for="reconciliation_time">Preferred time</label>
          <input type="time" name="reconciliation_time" id="reconciliation_time" value="{{ recon_time }}"
                 class="border rounded px-3 py-2 text-sm">
          <p class="text-xs text-slate-500 mt-1">
            Current server time: <strong>{{ server_time }}</strong> ({{ server_timezone }})
          </p>
        </div>

        <div class="text-sm text-slate-700">
          {% if recon_enabled and recon_next_run %}
            Next reconciliation: <strong>{{ recon_next_run.strftime('%Y-%m-%d %H:%M') }}</strong>
          {% elif not recon_enabled %}
            <span class="text-amber-600 font-medium">Paused</span>
          {% else %}
            <span class="text-slate-500">Not scheduled</span>
          {% endif %}
        </div>
      </div>

      <button type="submit" class="px-3 py-2 bg-slate-800 text-white rounded text-sm hover:bg-slate-700">
        Save reconciliation schedule
      </button>
    </form>

    {% if last_discovery_runs %}
    <div class="mt-4 pt-3 border-t">
      <h3 class="text-sm font-medium text-slate-600 mb-2">Last reconciliations</h3>
      <ul class="space-y-2 text-sm">
        {% for r in last_discovery_runs %}
        <li>
          <div class="flex justify-between items-start">
            <div>
              <span class="font-medium">#{{ r.run_id }}</span>
              <span class="{% if r.status == 'COMPLETED' %}text-green-700{% elif r.status == 'FAILED' %}text-red-700{% else %}text-slate-500{% endif %} font-medium">
                {{ r.status }}
              </span>
              <span class="text-slate-500 text-xs ml-1">{{ r.started_at.strftime('%Y-%m-%d %H:%M') if r.started_at else '' }}</span>
            </div>
            <div class="text-xs text-slate-500 text-right">
              {{ r.new_count or 0 }} new, {{ r.updated_count or 0 }} updated
            </div>
          </div>
          {% if r.error_summary %}
          <div class="text-xs text-red-700 mt-1 truncate" title="{{ r.error_summary }}">
            Error: {{ r.error_summary[:120] }}
          </div>
          {% endif %}
        </li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}
  </section>
```

- [ ] **Step 5: Run integration tests**

Run: `pytest tests/integration/test_schedule_settings.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add regwatch/web/routes/settings.py regwatch/web/templates/settings.html tests/integration/test_schedule_settings.py
git commit -m "feat(scheduler): add reconciliation schedule UI and route

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Final cleanup, lint, and verification

**Files:**
- Review: all changed files

- [ ] **Step 1: Run linting**

Run: `ruff check regwatch --fix`
Expected: No new errors (auto-fix import ordering if needed)

- [ ] **Step 2: Run type checking**

Run: `mypy regwatch`
Expected: No new errors

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Start the dev server and verify**

Run: `uvicorn regwatch.main:app --reload`

Verify in browser at `http://127.0.0.1:8001`:

1. **Dashboard**: Split button shows "Run all sources" + dropdown with 4 groups. Click "CSSF" — runs only CSSF sources.
2. **Settings — Scheduled Updates**: Toggle, frequency, time, next run, last checks with status colours and failed sources.
3. **Settings — Scheduled Reconciliation**: New section with toggle, frequency (default weekly), time (default 05:00), last 2 discovery runs.
4. **Settings — Recent pipeline runs**: Status colours (green/amber/red), failed source names, error messages, event + version counts.

- [ ] **Step 5: Commit any remaining fixes**

```bash
git add -u
git commit -m "chore: lint and cleanup for pipeline fixes

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```
