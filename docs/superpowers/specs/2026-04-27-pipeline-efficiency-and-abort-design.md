# Pipeline Efficiency (LLM Skip) & Cooperative Abort — Design Spec

**Date:** 2026-04-27
**Status:** Approved

## Goal

Stop spending LLM calls on documents whose content we have already ingested, and let the user abort an in-flight pipeline run from the web UI.

## Background — what runs the LLM today

The ingest pipeline is `Fetch → Extract → Match → Persist → Notify` (see `regwatch/pipeline/runner.py::PipelineRunner.run_once`). Of these phases, only **Match** ever calls the LLM:

- `CombinedMatcher.match` (`regwatch/pipeline/match/combined.py`): rules first (regex aliases, CELEX IDs, ELI URIs); LLM only as fallback when rules find nothing, latches off after the first error.
- `is_ict_document` (`regwatch/pipeline/match/classify.py`): keyword check first, LLM as fallback.
- `classify_entity_types` (`regwatch/pipeline/match/classify.py`): LLM-only (no rule path).
- `generate_description` (`regwatch/pipeline/match/classify.py`): source `description` field first, LLM as fallback.

Content-hash idempotency lives in `regwatch/pipeline/persist.py::persist_matched` (line 36). That means the LLM is invoked on every fetched document — including ones we have already seen — and the duplicate is only discarded after the LLM work is done.

## Non-goals

- Removing the LLM from new-document enrichment. Entity-type classification has no rule-based replacement and is load-bearing for filtering; we keep it.
- Hard-cancelling an in-flight document. Each document takes seconds; cooperative cancel between docs is enough.
- Adding an `ABORTED` status anywhere it does not already exist. The string is already used by `_abort_stale_runs` in `runner.py`.
- Changing the discovery subsystem (`regwatch/services/discovery.py`, `services/cssf_discovery.py`).

## 1. Skip LLM for already-seen content

### Change in execution order

Today (`runner.py:80-94`), each fetched raw document goes:

```
extract  →  match (LLM here)  →  persist (hash check here)
```

Move the hash check up:

```
extract  →  hash  →  in DB?
                       ├─ yes → skip; no MATCH, no LLM, no persist
                       └─ no  → match  →  persist
```

### Concretely

1. Extract `_text_for_hashing` and `_compute_content_hash` from `regwatch/pipeline/persist.py` into a small shared helper module `regwatch/pipeline/hashing.py` so the runner and `persist_matched` agree on the formula.
2. In `PipelineRunner.run_once`, after `_extract`:
   - Compute the hash from the `ExtractedDocument`.
   - Run a single `SELECT 1 FROM update_event WHERE content_hash = :hash LIMIT 1`.
   - If a row exists: increment `progress.docs_skipped`, log at DEBUG level, continue to the next raw document.
   - Otherwise: proceed with `_match` and `persist_matched` exactly as today.
3. The existing `select(UpdateEvent).where(...)` guard inside `persist_matched` stays as a safety net. `UpdateEvent.content_hash` is `unique=True`, so removing the guard would turn the documented "calling persist_matched twice is a no-op" contract (`tests/integration/test_persist.py::test_persist_is_idempotent`) into an `IntegrityError`. The runner's pre-check is the fast path; persist's guard is the contract.

### Edge cases

- **Empty extracted text.** `_text_for_hashing` returns `""` after stripping; the SHA-256 of an empty string is a fixed value. The first such doc per process gets matched/persisted; subsequent empty-text docs hit the cache and skip. This matches today's behaviour, which is correct.
- **Text-extraction failures.** Already handled by the per-document `except Exception` at `runner.py:95`. The hash check sits inside that try block, so a hashing failure is logged and the doc is skipped.
- **Concurrent inserts.** Not possible — the pipeline runs in a single thread per process, and the busy_timeout PRAGMA covers cross-process writers.

### Progress surfacing

`PipelineProgress` gains a `docs_skipped: int` field, reset by `reset_for_run`. The progress widget shows it as a small "(N already seen)" suffix next to `docs_seen`. The `pipeline_run` row does not need a new column — `events_created` already distinguishes "made a row" from "saw a doc".

## 2. Cooperative abort

### Mechanism

`PipelineProgress` gains a `cancel_event: threading.Event`. The runner checks it at two points only:

- Top of the source loop, before calling `source.fetch(...)`.
- Top of the per-document loop, before calling `_extract`.

If set, the runner:

1. Lets the *current* document finish (it has already started extract/match/persist atomically — interrupting risks partial state).
2. Breaks both loops.
3. Sets `run.status = "ABORTED"` and writes `run.finished_at`.
4. The progress object's `finish` is extended with an `aborted: bool = False` argument. When true, status becomes `"aborted"` and the message reads "Aborted by user — partial results kept."

### Wire-up

| Layer | Change |
|---|---|
| `PipelineProgress` | Add `cancel_event: threading.Event = field(default_factory=threading.Event)`, `request_cancel()` method that calls `cancel_event.set()`, `is_cancel_requested` property. `reset_for_run` calls `cancel_event.clear()`. Snapshot includes `cancel_requested: bool`. |
| `PipelineRunner.run_once` | Read `progress.cancel_event` if `progress is not None`. Check `.is_set()` between sources and between docs. Mark run `ABORTED` and break out cleanly when set. |
| `run_helpers.run_pipeline_background` | Pass `aborted=progress.is_cancel_requested` to `progress.finish` if the runner exited because of an abort (the runner returns the run id either way). |
| `actions.py` | Add `POST /run-pipeline/abort`. Reads `request.app.state.pipeline_progress`, calls `request_cancel()`, returns the same partial template as `/run-pipeline/status`. No-op if `status != "running"`. |
| `partials/pipeline_progress.html` | When `progress.status == "running"` and not `cancel_requested`, render an "Abort" button (`hx-post="/run-pipeline/abort"`, `hx-target="#pipeline-progress"`). When `cancel_requested` is true and still running, render a disabled "Aborting…" pill. When `status == "aborted"`, render the same way as `failed` but with the aborted message and amber instead of red. |

### State table

| `progress.status` | `cancel_requested` | UI shows |
|---|---|---|
| `idle` | (n/a) | "Run pipeline now" button |
| `running` | `false` | progress widget + "Abort" button |
| `running` | `true` | progress widget + disabled "Aborting…" |
| `aborted` | (n/a) | "Aborted by user — N events, M versions" |
| `completed` / `failed` | (n/a) | unchanged |

### `pipeline_run.status` values

Already supported: `RUNNING`, `COMPLETED`, `COMPLETED_WITH_ERRORS`. Add `ABORTED`. The `_abort_stale_runs` helper at `runner.py:108-113` already writes that exact string for stuck-on-startup rows, so the recent-runs UI in the settings page already needs to render it (the 2026-04-24 spec colour-codes `ABORTED` as red — keep that).

## Files changed

| File | Change |
|------|--------|
| `regwatch/pipeline/hashing.py` (new) | `text_for_hashing(extracted) -> str`, `content_hash(text) -> str` |
| `regwatch/pipeline/runner.py` | Hash check between extract and match; cancel-event checks between sources and between docs; `ABORTED` status path |
| `regwatch/pipeline/persist.py` | Use `hashing.py` for the hash formula. Keep the `UpdateEvent` existence guard as a safety net (the column is `unique=True` and `test_persist_is_idempotent` depends on it). |
| `regwatch/pipeline/progress.py` | `cancel_event`, `request_cancel()`, `is_cancel_requested`, `docs_skipped`; `finish(aborted=...)` |
| `regwatch/pipeline/run_helpers.py` | Pass abort state through to `progress.finish` |
| `regwatch/web/routes/actions.py` | New `POST /run-pipeline/abort` endpoint |
| `regwatch/web/templates/partials/pipeline_progress.html` | Abort button, "Aborting…" state, aborted-final state, "(N already seen)" suffix |
| `tests/unit/test_runner.py` | New test: pre-populate `update_event`, run pipeline, assert match/persist not called |
| `tests/unit/test_runner.py` | New test: set `cancel_event` between docs via a fake source generator, assert run ends with `ABORTED` and remaining docs untouched |
| `tests/integration/test_run_pipeline_action.py` | Run pipeline twice on the same source, assert second run makes zero LLM calls |
| `tests/integration/test_app_smoke.py` | New test: start a run, hit `/run-pipeline/abort`, assert widget reaches aborted state |

## Out of scope

- Hard-cancelling the in-flight document.
- A "force abort" that kills the worker thread.
- Persisting `docs_skipped` to the `pipeline_run` table (in-memory progress only).
- Adding skip logic to the discovery subsystem.
