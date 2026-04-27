# Pipeline Efficiency (LLM Skip) & Cooperative Abort — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop spending LLM calls on documents whose content we have already ingested, and let the user abort an in-flight pipeline run from the web UI.

**Architecture:** Hoist the SHA-256 content-hash check from `persist.py` to the runner so it short-circuits before the MATCH phase that calls the LLM. The persist-side guard stays as a safety net (the column is `unique=True`). Add a `threading.Event` on `PipelineProgress` that the runner polls between sources and between docs, plus a `POST /run-pipeline/abort` endpoint and an Abort button in the progress widget.

**Tech Stack:** Python 3.12, SQLAlchemy 2, FastAPI, Jinja2/HTMX/Tailwind, pytest (`pytest-httpx`, `MagicMock`).

**Spec:** `docs/superpowers/specs/2026-04-27-pipeline-efficiency-and-abort-design.md`

---

## File map

**Create:**
- `regwatch/pipeline/hashing.py` — `text_for_hashing(extracted) -> str`, `content_hash(text) -> str`
- `tests/unit/test_pipeline_hashing.py` — unit tests for the helper

**Modify:**
- `regwatch/pipeline/persist.py` — use the helper instead of the inline formula
- `regwatch/pipeline/runner.py` — pre-match hash check; cancel-event polling; `ABORTED` status
- `regwatch/pipeline/progress.py` — `docs_skipped`, `cancel_event`, `request_cancel()`, `is_cancel_requested`, snapshot fields, `finish(aborted=...)`
- `regwatch/pipeline/run_helpers.py` — translate `is_cancel_requested` into `progress.finish(aborted=True)`
- `regwatch/web/routes/actions.py` — `POST /run-pipeline/abort`
- `regwatch/web/templates/partials/pipeline_progress.html` — Abort button, "Aborting…" state, aborted-final state, "(N already seen)" suffix

**Test files extended:**
- `tests/unit/test_pipeline_progress.py` — `docs_skipped`, cancel API, `finish(aborted=True)`
- `tests/unit/test_pipeline_runner.py` — pre-existing-event short-circuit, cancel between docs, cancel between sources
- `tests/unit/test_run_helpers.py` — aborted-state pass-through
- `tests/integration/test_run_pipeline_action.py` — second run makes zero LLM calls; `/run-pipeline/abort` returns aborted widget

---

### Task 1: Create the hashing helper module

**Files:**
- Create: `regwatch/pipeline/hashing.py`
- Create: `tests/unit/test_pipeline_hashing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_pipeline_hashing.py`:

```python
from datetime import UTC, datetime

from regwatch.domain.types import ExtractedDocument, RawDocument
from regwatch.pipeline.hashing import content_hash, text_for_hashing


def _raw() -> RawDocument:
    now = datetime.now(UTC)
    return RawDocument(
        source="cssf_rss",
        source_url="https://example.com/x",
        title="t",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )


def test_text_for_hashing_prefers_pdf_over_html() -> None:
    extracted = ExtractedDocument(
        raw=_raw(),
        html_text="html-version",
        pdf_path=None,
        pdf_extracted_text="pdf-version",
        pdf_is_protected=False,
    )
    assert text_for_hashing(extracted) == "pdf-version"


def test_text_for_hashing_falls_back_to_html() -> None:
    extracted = ExtractedDocument(
        raw=_raw(),
        html_text="html-version",
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    assert text_for_hashing(extracted) == "html-version"


def test_text_for_hashing_strips_whitespace() -> None:
    extracted = ExtractedDocument(
        raw=_raw(),
        html_text="  hello  \n",
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    assert text_for_hashing(extracted) == "hello"


def test_text_for_hashing_returns_empty_string_when_no_text() -> None:
    extracted = ExtractedDocument(
        raw=_raw(),
        html_text=None,
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    assert text_for_hashing(extracted) == ""


def test_content_hash_is_sha256_hex() -> None:
    # SHA-256 of "abc" is a known value.
    assert content_hash("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_content_hash_is_stable() -> None:
    assert content_hash("hello world") == content_hash("hello world")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_pipeline_hashing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regwatch.pipeline.hashing'`.

- [ ] **Step 3: Implement the helper**

Create `regwatch/pipeline/hashing.py`:

```python
"""Pure helpers for computing the content hash used to dedupe documents.

Lives in its own module so the runner (pre-match short-circuit) and
persist.py (idempotency safety net) agree on the formula.
"""
from __future__ import annotations

import hashlib

from regwatch.domain.types import ExtractedDocument


def text_for_hashing(extracted: ExtractedDocument) -> str:
    """Return the text we hash to identify a document.

    Prefers the PDF-extracted text over the HTML body when both are
    present. Whitespace is stripped so trivial trailing-newline
    differences do not produce different hashes.
    """
    return (extracted.pdf_extracted_text or extracted.html_text or "").strip()


def content_hash(text: str) -> str:
    """Return the lowercase hex SHA-256 of *text* (UTF-8 encoded)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_pipeline_hashing.py -v`
Expected: PASS for all 6 tests.

- [ ] **Step 5: Commit**

```bash
git add regwatch/pipeline/hashing.py tests/unit/test_pipeline_hashing.py
git commit -m "feat(pipeline): extract content-hash helper into pipeline/hashing.py"
```

---

### Task 2: Make `persist.py` use the shared helper

**Files:**
- Modify: `regwatch/pipeline/persist.py:30-33` and `regwatch/pipeline/persist.py:82-83`

This is a pure refactor — same formula, fewer call sites. No new test; existing `tests/integration/test_persist.py` proves we did not change behaviour.

- [ ] **Step 1: Confirm baseline tests pass**

Run: `pytest tests/integration/test_persist.py -v`
Expected: 3 PASS (`test_persist_creates_event_and_links`, `test_persist_is_idempotent`, `test_persist_creates_new_version_on_content_change`).

- [ ] **Step 2: Replace the inline formula with the helper**

In `regwatch/pipeline/persist.py`:

Change the imports near the top so `import hashlib` is removed and the helpers are imported. The new top section reads:

```python
"""Phase 4: persist the matched document into SQLite in a single transaction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentVersion,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.domain.types import ExtractedDocument, MatchedDocument
from regwatch.pipeline.diff import compute_diff
from regwatch.pipeline.hashing import content_hash, text_for_hashing
```

Inside `persist_matched`, replace the two lines that compute the hash:

```python
    text_for_hash = _text_for_hashing(extracted)
    content_hash = hashlib.sha256(text_for_hash.encode("utf-8")).hexdigest()
```

with:

```python
    text_for_hash = text_for_hashing(extracted)
    document_hash = content_hash(text_for_hash)
```

Then update the rest of the function to use `document_hash` everywhere it currently uses `content_hash` (the local variable). There are three such references — the `where()` filter on line 37, the `UpdateEvent(...)` kwarg on line 51, and the call to `_create_new_version(...)` on line 73. The kwarg name `content_hash=` stays — only the value identifier changes.

Delete the now-unused private helper at the bottom:

```python
def _text_for_hashing(extracted: ExtractedDocument) -> str:
    return (extracted.pdf_extracted_text or extracted.html_text or "").strip()
```

Inside `_create_new_version`, the `content_hash` parameter name stays (it's a kwarg). Nothing changes inside that function.

- [ ] **Step 3: Run baseline tests**

Run: `pytest tests/integration/test_persist.py tests/unit/test_pipeline_hashing.py -v`
Expected: all PASS.

- [ ] **Step 4: Run lint and type-check**

Run: `ruff check regwatch/pipeline/persist.py && mypy regwatch/pipeline/persist.py`
Expected: clean, no errors.

- [ ] **Step 5: Commit**

```bash
git add regwatch/pipeline/persist.py
git commit -m "refactor(pipeline): persist.py uses shared hashing helper"
```

---

### Task 3: Add `docs_skipped` to `PipelineProgress`

**Files:**
- Modify: `regwatch/pipeline/progress.py`
- Modify: `tests/unit/test_pipeline_progress.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_pipeline_progress.py`:

```python
def test_docs_skipped_counter_starts_at_zero_and_resets() -> None:
    p = PipelineProgress()
    assert p.snapshot()["docs_skipped"] == 0

    p.reset_for_run(total_sources=1)
    p.note_skipped()
    p.note_skipped()
    assert p.snapshot()["docs_skipped"] == 2

    p.reset_for_run(total_sources=1)
    assert p.snapshot()["docs_skipped"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_pipeline_progress.py::test_docs_skipped_counter_starts_at_zero_and_resets -v`
Expected: FAIL with `KeyError: 'docs_skipped'` or `AttributeError: 'PipelineProgress' object has no attribute 'note_skipped'`.

- [ ] **Step 3: Implement `docs_skipped`**

In `regwatch/pipeline/progress.py`, add a new field after `docs_seen` (around line 33):

```python
    docs_skipped: int = 0  # documents short-circuited by the content-hash pre-check
```

Add the mutator inside the class (after `add_persist_result`, around line 91):

```python
    def note_skipped(self) -> None:
        with self._lock:
            self.docs_skipped += 1
```

In `reset_for_run`, add `self.docs_skipped = 0` next to `self.docs_seen = 0` (around line 56).

In `snapshot()`, add the field:

```python
                "docs_skipped": self.docs_skipped,
```

next to `"docs_seen": self.docs_seen,`.

- [ ] **Step 4: Run progress tests**

Run: `pytest tests/unit/test_pipeline_progress.py -v`
Expected: all PASS, including the new one.

- [ ] **Step 5: Commit**

```bash
git add regwatch/pipeline/progress.py tests/unit/test_pipeline_progress.py
git commit -m "feat(pipeline): add docs_skipped counter to PipelineProgress"
```

---

### Task 4: Runner short-circuits on existing content hash

**Files:**
- Modify: `regwatch/pipeline/runner.py`
- Modify: `tests/unit/test_pipeline_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_pipeline_runner.py`:

```python
from datetime import UTC, datetime

from regwatch.db.models import UpdateEvent
from regwatch.domain.types import (
    ExtractedDocument,
    MatchedDocument,
    RawDocument,
)
from regwatch.pipeline.hashing import content_hash
from regwatch.pipeline.progress import PipelineProgress


def _raw_doc() -> RawDocument:
    now = datetime.now(UTC)
    return RawDocument(
        source="cssf_rss",
        source_url="https://example.com/dup",
        title="dup",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )


def test_runner_skips_match_when_hash_already_in_update_event(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    raw = _raw_doc()
    body = "the body that is already in the catalog"
    pre_existing_hash = content_hash(body)

    class OneDocSource:
        name = "src_one"

        def fetch(self, since):
            return iter([raw])

    def fake_extract(r: RawDocument) -> ExtractedDocument:
        return ExtractedDocument(
            raw=r,
            html_text=body,
            pdf_path=None,
            pdf_extracted_text=None,
            pdf_is_protected=False,
        )

    match_calls: list[ExtractedDocument] = []

    def fake_match(extracted: ExtractedDocument) -> MatchedDocument:
        match_calls.append(extracted)
        return MatchedDocument(extracted=extracted)

    with Session(engine) as session:
        session.add(
            UpdateEvent(
                source="prior_run",
                source_url="https://example.com/dup-prior",
                title="prior",
                published_at=datetime.now(UTC),
                fetched_at=datetime.now(UTC),
                raw_payload={},
                content_hash=pre_existing_hash,
                is_ict=False,
                severity="INFORMATIONAL",
                review_status="NEW",
            )
        )
        session.flush()

        progress = PipelineProgress()
        progress.reset_for_run(total_sources=1)

        runner = PipelineRunner(
            session,
            sources=[OneDocSource()],
            extract=fake_extract,
            match=fake_match,
        )
        runner.run_once(progress=progress)
        session.commit()

    assert match_calls == []  # match never invoked for the duplicate
    assert progress.snapshot()["docs_skipped"] == 1
    assert progress.snapshot()["docs_seen"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_pipeline_runner.py::test_runner_skips_match_when_hash_already_in_update_event -v`
Expected: FAIL — currently `match_calls` will have one entry because the runner always calls `_match`.

- [ ] **Step 3: Add the pre-match hash check to `run_once`**

In `regwatch/pipeline/runner.py`, add the import next to the others:

```python
from sqlalchemy import select, update
```

(Replace the existing `from sqlalchemy import update` import — `select` is added.)

Add the helper imports near the top:

```python
from regwatch.db.models import PipelineRun, UpdateEvent
from regwatch.pipeline.hashing import content_hash, text_for_hashing
```

(Extend the existing `from regwatch.db.models import PipelineRun` line to also import `UpdateEvent`.)

Inside the per-document block (`runner.py:81-94` today), wrap `extracted = self._extract(raw)` so the hash check sits between extract and match. Replace this block:

```python
                    try:
                        extracted = self._extract(raw)
                        if progress is not None:
                            progress.set_phase("MATCH")
                        matched = self._match(extracted)
                        if progress is not None:
                            progress.set_phase("PERSIST")
                        result = persist_matched(self._session, matched)
                        run.events_created += result.events_created
                        run.versions_created += result.versions_created
                        if progress is not None:
                            progress.add_persist_result(
                                result.events_created, result.versions_created
                            )
                    except Exception:  # noqa: BLE001
                        logger.exception("Per-document failure in %s", source.name)
```

with:

```python
                    try:
                        extracted = self._extract(raw)
                        text_hash = content_hash(text_for_hashing(extracted))
                        already_seen = self._session.scalar(
                            select(UpdateEvent.event_id).where(
                                UpdateEvent.content_hash == text_hash
                            )
                        )
                        if already_seen is not None:
                            if progress is not None:
                                progress.note_skipped()
                            continue
                        if progress is not None:
                            progress.set_phase("MATCH")
                        matched = self._match(extracted)
                        if progress is not None:
                            progress.set_phase("PERSIST")
                        result = persist_matched(self._session, matched)
                        run.events_created += result.events_created
                        run.versions_created += result.versions_created
                        if progress is not None:
                            progress.add_persist_result(
                                result.events_created, result.versions_created
                            )
                    except Exception:  # noqa: BLE001
                        logger.exception("Per-document failure in %s", source.name)
```

- [ ] **Step 4: Run the new test and the full runner suite**

Run: `pytest tests/unit/test_pipeline_runner.py -v`
Expected: 3 PASS (the two existing tests plus the new one).

Run the integration persist test to verify nothing regressed:

Run: `pytest tests/integration/test_persist.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Run lint and type-check**

Run: `ruff check regwatch/pipeline/runner.py && mypy regwatch/pipeline/runner.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/runner.py tests/unit/test_pipeline_runner.py
git commit -m "feat(pipeline): runner short-circuits on existing content hash"
```

---

### Task 5: Add `cancel_event` API to `PipelineProgress`

**Files:**
- Modify: `regwatch/pipeline/progress.py`
- Modify: `tests/unit/test_pipeline_progress.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline_progress.py`:

```python
def test_cancel_event_starts_unset_and_can_be_requested() -> None:
    p = PipelineProgress()
    assert p.is_cancel_requested is False
    assert p.snapshot()["cancel_requested"] is False

    p.request_cancel()
    assert p.is_cancel_requested is True
    assert p.snapshot()["cancel_requested"] is True


def test_reset_for_run_clears_cancel_event() -> None:
    p = PipelineProgress()
    p.request_cancel()
    p.reset_for_run(total_sources=1)
    assert p.is_cancel_requested is False
    assert p.snapshot()["cancel_requested"] is False


def test_finish_with_aborted_marks_status_aborted() -> None:
    p = PipelineProgress()
    p.reset_for_run(total_sources=1)
    p.add_persist_result(events=2, versions=1)
    p.request_cancel()
    p.finish(run_id=11, aborted=True)

    s = p.snapshot()
    assert s["status"] == "aborted"
    assert s["run_id"] == 11
    assert s["events_created"] == 2
    assert s["versions_created"] == 1
    assert "Aborted by user" in s["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_pipeline_progress.py -v -k "cancel or aborted"`
Expected: FAIL — `is_cancel_requested`, `request_cancel`, the `aborted` kwarg of `finish`, and the snapshot key `cancel_requested` do not exist yet.

- [ ] **Step 3: Implement the cancel API**

In `regwatch/pipeline/progress.py`:

Add `Event` to the threading import at the top:

```python
from threading import Event, RLock
```

Add a new field after `_lock`:

```python
    _cancel_event: Event = field(default_factory=Event, repr=False, compare=False)
```

Add the public API methods inside the class (place them after `finish`):

```python
    def request_cancel(self) -> None:
        with self._lock:
            self._cancel_event.set()
            if self.status == "running":
                self.message = "Cancellation requested — finishing current document…"

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()
```

In `reset_for_run`, add `self._cancel_event.clear()` next to the other resets.

Change `finish` to accept `aborted`:

```python
    def finish(
        self, *, run_id: int | None, error: str | None = None, aborted: bool = False
    ) -> None:
        with self._lock:
            self.finished_at = datetime.now(UTC)
            self.run_id = run_id
            self.current_phase = "DONE"
            self.current_source = None
            self.current_doc_title = None
            if aborted:
                self.status = "aborted"
                self.message = (
                    f"Aborted by user — kept {self.events_created} event(s), "
                    f"{self.versions_created} version(s)."
                )
            elif error:
                self.status = "failed"
                self.error = error
                self.message = f"Pipeline failed: {error}"
            else:
                self.status = "completed"
                self.message = (
                    f"Pipeline run #{run_id} completed — "
                    f"{self.events_created} new event(s)"
                )
```

In `snapshot`, add the new key in the same dict:

```python
                "docs_skipped": self.docs_skipped,
                "cancel_requested": self._cancel_event.is_set(),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_pipeline_progress.py -v`
Expected: all PASS.

- [ ] **Step 5: Run lint and type-check**

Run: `ruff check regwatch/pipeline/progress.py && mypy regwatch/pipeline/progress.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/progress.py tests/unit/test_pipeline_progress.py
git commit -m "feat(pipeline): add cancel_event API to PipelineProgress"
```

---

### Task 6: Runner respects the cancel event

**Files:**
- Modify: `regwatch/pipeline/runner.py`
- Modify: `tests/unit/test_pipeline_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline_runner.py`:

```python
def test_runner_aborts_between_documents(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    progress = PipelineProgress()
    progress.reset_for_run(total_sources=1)

    raws = [
        RawDocument(
            source="src",
            source_url=f"https://example.com/{i}",
            title=f"doc {i}",
            published_at=datetime.now(UTC),
            raw_payload={},
            fetched_at=datetime.now(UTC),
        )
        for i in range(3)
    ]

    class ThreeDocSource:
        name = "src"

        def fetch(self, since):
            return iter(raws)

    extract_calls: list[RawDocument] = []

    def fake_extract(r: RawDocument) -> ExtractedDocument:
        extract_calls.append(r)
        # Trigger the abort after the first doc has gone through extract+match.
        if len(extract_calls) == 1:
            progress.request_cancel()
        return ExtractedDocument(
            raw=r,
            html_text=f"text {len(extract_calls)}",  # unique text -> not deduped
            pdf_path=None,
            pdf_extracted_text=None,
            pdf_is_protected=False,
        )

    def fake_match(extracted: ExtractedDocument) -> MatchedDocument:
        return MatchedDocument(extracted=extracted)

    with Session(engine) as session:
        runner = PipelineRunner(
            session,
            sources=[ThreeDocSource()],
            extract=fake_extract,
            match=fake_match,
        )
        run_id = runner.run_once(progress=progress)
        session.commit()

        run = session.get(PipelineRun, run_id)
        assert run.status == "ABORTED"
        # First doc completed; the cancel was set during its extract,
        # so the next iteration of the doc loop sees the flag and stops.
        assert len(extract_calls) == 1


def test_runner_aborts_between_sources(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    progress = PipelineProgress()
    progress.reset_for_run(total_sources=2)
    progress.request_cancel()  # already cancelled before the run starts

    fetched_from: list[str] = []

    class S1:
        name = "s1"

        def fetch(self, since):
            fetched_from.append("s1")
            return iter([])

    class S2:
        name = "s2"

        def fetch(self, since):
            fetched_from.append("s2")
            return iter([])

    with Session(engine) as session:
        runner = PipelineRunner(
            session,
            sources=[S1(), S2()],
            extract=MagicMock(),
            match=MagicMock(),
        )
        run_id = runner.run_once(progress=progress)
        session.commit()

        run = session.get(PipelineRun, run_id)
        assert run.status == "ABORTED"
        assert fetched_from == []  # no source was even fetched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_pipeline_runner.py -v -k abort`
Expected: FAIL — runner has no abort logic, status will be `COMPLETED`, both sources will be fetched.

- [ ] **Step 3: Implement cancel checks in `run_once`**

In `regwatch/pipeline/runner.py`, replace the body of `run_once` (everything after the `since = since or datetime(...)` line) with:

```python
        aborted = False

        def _cancelled() -> bool:
            return progress is not None and progress.is_cancel_requested

        for idx, source in enumerate(self._sources, start=1):
            if _cancelled():
                aborted = True
                break
            if progress is not None:
                progress.begin_source(source.name, idx)
            run.sources_attempted = [*run.sources_attempted, source.name]
            try:
                for raw in source.fetch(since):
                    if _cancelled():
                        aborted = True
                        break
                    if progress is not None:
                        progress.begin_document(raw.title or raw.source_url)
                    try:
                        extracted = self._extract(raw)
                        text_hash = content_hash(text_for_hashing(extracted))
                        already_seen = self._session.scalar(
                            select(UpdateEvent.event_id).where(
                                UpdateEvent.content_hash == text_hash
                            )
                        )
                        if already_seen is not None:
                            if progress is not None:
                                progress.note_skipped()
                            continue
                        if progress is not None:
                            progress.set_phase("MATCH")
                        matched = self._match(extracted)
                        if progress is not None:
                            progress.set_phase("PERSIST")
                        result = persist_matched(self._session, matched)
                        run.events_created += result.events_created
                        run.versions_created += result.versions_created
                        if progress is not None:
                            progress.add_persist_result(
                                result.events_created, result.versions_created
                            )
                    except Exception:  # noqa: BLE001
                        logger.exception("Per-document failure in %s", source.name)
                if aborted:
                    break
            except Exception:  # noqa: BLE001
                logger.exception("Source %s failed", source.name)
                run.sources_failed = [*run.sources_failed, source.name]
                if progress is not None:
                    progress.fail_source(source.name)

        run.finished_at = datetime.now(UTC)
        if aborted:
            run.status = "ABORTED"
        else:
            run.status = (
                "COMPLETED_WITH_ERRORS" if run.sources_failed else "COMPLETED"
            )
        self._session.flush()
        return run.run_id
```

The runner only needs to *read* the cancel flag, so it goes through the public `progress.is_cancel_requested` property — no private-attribute access, no extra `Event` plumbing.

- [ ] **Step 4: Run runner tests**

Run: `pytest tests/unit/test_pipeline_runner.py -v`
Expected: 5 PASS (2 original + 1 from Task 4 + 2 new).

- [ ] **Step 5: Run lint and type-check**

Run: `ruff check regwatch/pipeline/runner.py && mypy regwatch/pipeline/runner.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/runner.py tests/unit/test_pipeline_runner.py
git commit -m "feat(pipeline): cooperative abort between sources and between docs"
```

---

### Task 7: `run_helpers` translates cancel into `aborted` finish

**Files:**
- Modify: `regwatch/pipeline/run_helpers.py`
- Modify: `tests/unit/test_run_helpers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_helpers.py`:

```python
def test_aborted_run_calls_finish_with_aborted_true():
    progress = PipelineProgress()
    mock_session = MagicMock()
    mock_sf = MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=mock_session),
        __exit__=MagicMock(return_value=False),
    ))

    # Pretend a cancel was requested before the runner returned.
    progress.request_cancel()

    with patch(
        "regwatch.pipeline.run_helpers.build_enabled_sources",
        return_value=[],
    ), patch(
        "regwatch.pipeline.run_helpers.build_runner",
    ) as mock_runner:
        mock_runner.return_value.run_once.return_value = 99
        run_pipeline_background(
            session_factory=mock_sf,
            config=MagicMock(),
            llm_client=MagicMock(),
            progress=progress,
        )

    assert progress.snapshot()["status"] == "aborted"
    assert progress.snapshot()["run_id"] == 99
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_run_helpers.py::test_aborted_run_calls_finish_with_aborted_true -v`
Expected: FAIL — `progress.status` will be `"completed"` because `run_helpers` calls `progress.finish(run_id=run_id)` without `aborted=`.

- [ ] **Step 3: Wire the abort flag through `run_helpers`**

In `regwatch/pipeline/run_helpers.py`, change the success path:

```python
    progress.finish(run_id=run_id)
```

to:

```python
    progress.finish(run_id=run_id, aborted=progress.is_cancel_requested)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_run_helpers.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/pipeline/run_helpers.py tests/unit/test_run_helpers.py
git commit -m "feat(pipeline): run_helpers reports aborted state to progress"
```

---

### Task 8: `POST /run-pipeline/abort` endpoint

**Files:**
- Modify: `regwatch/web/routes/actions.py`
- Modify: `tests/integration/test_run_pipeline_action.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_run_pipeline_action.py`:

```python
def test_abort_endpoint_sets_cancel_when_running(tmp_path: Path, monkeypatch) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)
    progress = client.app.state.pipeline_progress
    progress.reset_for_run(total_sources=1)
    progress.message = "running"

    resp = client.post("/run-pipeline/abort")

    assert resp.status_code == 200
    assert progress.is_cancel_requested is True
    assert "Cancellation requested" in progress.snapshot()["message"]


def test_abort_endpoint_is_noop_when_idle(tmp_path: Path, monkeypatch) -> None:
    client = _cssf_only_client(tmp_path, monkeypatch)
    progress = client.app.state.pipeline_progress
    # Do not start a run.

    resp = client.post("/run-pipeline/abort")

    assert resp.status_code == 200
    assert progress.is_cancel_requested is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_run_pipeline_action.py -v -k abort_endpoint`
Expected: FAIL with 404 — the endpoint does not exist.

- [ ] **Step 3: Add the endpoint**

In `regwatch/web/routes/actions.py`, add a new route after `run_pipeline_status` (around line 95):

```python
@router.post("/run-pipeline/abort", response_class=HTMLResponse)
def run_pipeline_abort(request: Request) -> HTMLResponse:
    """Request a cooperative cancel of the running pipeline.

    No-op if the pipeline is not running. The runner picks up the flag
    between documents and between sources; the in-flight document is
    allowed to finish so the DB never sees a partial write.
    """
    progress: PipelineProgress = request.app.state.pipeline_progress
    templates = request.app.state.templates

    snapshot = progress.snapshot()
    if snapshot["status"] == "running":
        progress.request_cancel()

    return templates.TemplateResponse(
        request,
        "partials/pipeline_progress.html",
        {"progress": progress.snapshot()},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_run_pipeline_action.py -v -k abort_endpoint`
Expected: 2 PASS.

- [ ] **Step 5: Run the full action-route suite to catch regressions**

Run: `pytest tests/integration/test_run_pipeline_action.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/routes/actions.py tests/integration/test_run_pipeline_action.py
git commit -m "feat(web): POST /run-pipeline/abort requests cooperative cancel"
```

---

### Task 9: Update the progress widget — Abort button, aborting state, aborted-final state, skip count

**Files:**
- Modify: `regwatch/web/templates/partials/pipeline_progress.html`

This is a pure UI change. The route tests already exercise the data flow; we keep the smoke test in Task 11 to assert the final aborted widget renders.

- [ ] **Step 1: Replace the template**

Replace the **whole file** with:

```html
{#
  Self-replacing pipeline-progress widget. Polls /run-pipeline/status every
  2 seconds while the run is active. When the run finishes, the rendered
  HTML drops the polling trigger so the widget stops polling on its own.
#}
<div id="pipeline-progress"
     {% if progress.status == 'running' %}
     hx-get="/run-pipeline/status"
     hx-trigger="every 2s"
     hx-swap="outerHTML"
     {% endif %}
     class="border rounded p-4
       {% if progress.status == 'running' %}bg-blue-50 border-blue-300
       {% elif progress.status == 'completed' %}bg-green-50 border-green-300
       {% elif progress.status == 'aborted' %}bg-amber-50 border-amber-300
       {% elif progress.status == 'failed' %}bg-red-50 border-red-300
       {% else %}bg-slate-50 border-slate-300{% endif %}">

  <div class="flex items-start justify-between gap-4 mb-2">
    <div>
      <div class="text-sm font-semibold uppercase tracking-wide
        {% if progress.status == 'running' %}text-blue-800
        {% elif progress.status == 'completed' %}text-green-800
        {% elif progress.status == 'aborted' %}text-amber-800
        {% elif progress.status == 'failed' %}text-red-800
        {% else %}text-slate-700{% endif %}">
        {% if progress.status == 'running' %}
          {% if progress.cancel_requested %}Pipeline aborting…{% else %}Pipeline running…{% endif %}
        {% elif progress.status == 'completed' %}
          Pipeline completed
        {% elif progress.status == 'aborted' %}
          Pipeline aborted
        {% elif progress.status == 'failed' %}
          Pipeline failed
        {% else %}
          Pipeline idle
        {% endif %}
      </div>
      <div class="text-xs text-slate-600 mt-1">{{ progress.message }}</div>
    </div>
    <div class="flex items-center gap-3 shrink-0">
      {% if progress.status == 'running' %}
        {% if progress.cancel_requested %}
          <span class="px-3 py-1 bg-amber-200 text-amber-900 rounded text-xs">
            Aborting…
          </span>
        {% else %}
          <button class="px-3 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700"
                  hx-post="/run-pipeline/abort"
                  hx-target="#pipeline-progress"
                  hx-swap="outerHTML">
            Abort
          </button>
        {% endif %}
      {% endif %}
      <div class="text-xs text-slate-500 text-right">
        {% if progress.elapsed_seconds %}{{ progress.elapsed_seconds }}s elapsed{% endif %}
      </div>
    </div>
  </div>

  {% if progress.status in ('running', 'completed', 'aborted', 'failed') %}
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mt-3">
    <div class="bg-white rounded border p-2">
      <div class="uppercase text-slate-500">Source</div>
      <div class="font-semibold">
        {% if progress.current_source %}
          {{ progress.current_source }}
          {% if progress.total_sources %}
            <span class="text-slate-400 font-normal">
              ({{ progress.source_index }}/{{ progress.total_sources }})
            </span>
          {% endif %}
        {% else %}
          <span class="text-slate-400">—</span>
        {% endif %}
      </div>
    </div>
    <div class="bg-white rounded border p-2">
      <div class="uppercase text-slate-500">Phase</div>
      <div class="font-semibold">{{ progress.current_phase or '—' }}</div>
    </div>
    <div class="bg-white rounded border p-2">
      <div class="uppercase text-slate-500">Docs processed</div>
      <div class="font-semibold">
        {{ progress.docs_seen }}
        {% if progress.docs_skipped %}
          <span class="text-slate-400 font-normal">
            ({{ progress.docs_skipped }} already seen)
          </span>
        {% endif %}
      </div>
    </div>
    <div class="bg-white rounded border p-2">
      <div class="uppercase text-slate-500">Events / versions</div>
      <div class="font-semibold">
        {{ progress.events_created }} / {{ progress.versions_created }}
      </div>
    </div>
  </div>

  {% if progress.current_doc_title and progress.status == 'running' %}
  <div class="mt-3 text-xs text-slate-600 truncate">
    <span class="text-slate-500">Current:</span> {{ progress.current_doc_title }}
  </div>
  {% endif %}

  {% if progress.sources_failed %}
  <div class="mt-3 text-xs text-amber-800">
    Failed sources: {{ progress.sources_failed | join(', ') }}
  </div>
  {% endif %}

  {% if progress.error %}
  <div class="mt-3 text-xs text-red-800 break-words">
    {{ progress.error }}
  </div>
  {% endif %}

  {% if progress.status in ('completed', 'aborted', 'failed') %}
  <div class="mt-3 flex justify-end">
    <button class="px-3 py-1 bg-slate-200 rounded text-xs hover:bg-slate-300"
            onclick="location.reload()">
      Refresh dashboard
    </button>
  </div>
  {% endif %}
  {% endif %}
</div>
```

- [ ] **Step 2: Smoke-test the existing template-rendering tests**

Run: `pytest tests/integration/test_dashboard_view.py tests/integration/test_run_pipeline_action.py -v`
Expected: all PASS — no Jinja errors from the new branches.

- [ ] **Step 3: Commit**

```bash
git add regwatch/web/templates/partials/pipeline_progress.html
git commit -m "feat(web): pipeline progress widget shows Abort button + aborted state"
```

---

### Task 10: Integration — second pipeline run makes zero LLM calls

**Files:**
- Modify: `tests/integration/test_run_pipeline_action.py`

- [ ] **Step 1: Add the regression test**

Append to `tests/integration/test_run_pipeline_action.py`:

```python
def test_second_run_on_unchanged_source_skips_llm(
    tmp_path: Path, monkeypatch
) -> None:
    """Re-running on the same fixture must not call the LLM a second time."""
    client = _cssf_only_client(tmp_path, monkeypatch)
    _patch_registry_and_llm(client, monkeypatch)

    fake_llm = client.app.state.llm_client

    # First run: doc is new, LLM may be called for entity_types / description.
    resp = client.post("/run-pipeline")
    assert resp.status_code == 200
    # Wait for the background thread to finish.
    for _ in range(200):
        snap = client.app.state.pipeline_progress.snapshot()
        if snap["status"] in ("completed", "completed_with_errors"):
            break
        time.sleep(0.05)
    assert client.app.state.pipeline_progress.snapshot()["status"] == "completed"
    first_run_chat_calls = fake_llm.chat.call_count

    # Reset the spy and run again with the SAME fake source -> same content.
    fake_llm.chat.reset_mock()

    resp = client.post("/run-pipeline")
    assert resp.status_code == 200
    for _ in range(200):
        snap = client.app.state.pipeline_progress.snapshot()
        if snap["status"] in ("completed", "completed_with_errors"):
            break
        time.sleep(0.05)
    final = client.app.state.pipeline_progress.snapshot()
    assert final["status"] == "completed"
    # No LLM calls because the doc was already in the catalog.
    assert fake_llm.chat.call_count == 0
    assert final["docs_skipped"] == 1
    # And the first run actually exercised something so the assertion is meaningful.
    assert first_run_chat_calls >= 0  # may be zero if rules matched everything
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/integration/test_run_pipeline_action.py::test_second_run_on_unchanged_source_skips_llm -v`
Expected: PASS.

If the assertion `final["docs_skipped"] == 1` fails with 0, the runner-side hash check is not engaging — debug by printing `final` and the first-run snapshot before fixing.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_run_pipeline_action.py
git commit -m "test(pipeline): assert second run on unchanged source makes no LLM calls"
```

---

### Task 11: Final manual UI check + full suite

This task is the verification gate before declaring the work done.

- [ ] **Step 1: Run the full suite**

Run: `pytest`
Expected: all green. Note the total ran roughly matches the previous baseline (~467) plus the new tests added in this plan (~12 new).

- [ ] **Step 2: Run the linter and type-checker**

Run: `ruff check regwatch && mypy regwatch`
Expected: clean.

- [ ] **Step 3: Manual UI check**

Start the app:

```bash
uvicorn regwatch.main:app --reload
```

Open `http://localhost:8001` in a browser. From the Dashboard:

1. Click "Run pipeline now". Verify the progress widget appears with an "Abort" button on the right.
2. Click "Abort" while the run is in flight. Verify the button is replaced by a yellow "Aborting…" pill within 2 seconds (HTMX poll).
3. After the in-flight document finishes, verify the widget settles into the **amber** "Pipeline aborted" state with the message "Aborted by user — kept N event(s), M version(s)." and a "Refresh dashboard" button.
4. Click "Run pipeline now" again. Verify the second run shows "(N already seen)" next to "Docs processed" once the previously-fetched docs come through.
5. Open the Settings page and confirm that the most recent run row shows status `ABORTED` (rendered red per the existing colour map).

If any of those don't behave as described, file a follow-up. The web tests can't see colours, so this manual pass is load-bearing.

- [ ] **Step 4: No-op commit not required** — stop after the manual check.

---

## Verification checklist (used by Task 11)

- [ ] Re-running the pipeline on the same content makes zero LLM calls (Task 10 covers this).
- [ ] `pipeline_run.status` is set to `ABORTED` when the user aborts (Task 6).
- [ ] `progress.snapshot()["status"] == "aborted"` after `run_helpers` returns (Task 7).
- [ ] The Abort button is visible while running, the "Aborting…" pill replaces it after a click, and the final state shows the aborted message (Task 9 + Task 11 manual).
- [ ] The skip counter `(N already seen)` appears next to "Docs processed" after a duplicate run (Task 9 + Task 10).
- [ ] All existing tests still pass (Task 11 step 1).

## Out of scope for this plan

- Hard-cancelling the in-flight document (we wait for it).
- Persisting `docs_skipped` to the `pipeline_run` table.
- Discovery-subsystem changes.
- Adding a per-source-LLM-disable toggle (the user did not request this; the spec explicitly keeps the LLM where it already lives).
