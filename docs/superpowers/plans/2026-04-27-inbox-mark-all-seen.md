# Inbox "Mark All as Seen" Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the user a one-click "Mark all as seen" button on `/inbox` that bulk-updates every `update_event` row with `review_status='NEW'` to `'SEEN'`. No filter awareness — operates on every NEW event.

**Architecture:** A new `InboxService.mark_all_seen()` method runs a single SQL `UPDATE … WHERE review_status='NEW'` and returns `rowcount`. A new `POST /inbox/mark-all-seen` calls it and returns a 303 redirect to `/inbox`. The inbox template gains a small button in the page header, rendered only when there are NEW events.

**Tech Stack:** Python 3.12, SQLAlchemy 2 (bulk `update()`), FastAPI, Jinja2/Tailwind, pytest.

**Spec:** `docs/superpowers/specs/2026-04-27-inbox-mark-all-seen-design.md`

---

## File map

**Modify:**
- `regwatch/services/inbox.py` — add `mark_all_seen` method + the `update` import.
- `regwatch/web/routes/inbox.py` — add `POST /inbox/mark-all-seen` + `RedirectResponse` import.
- `regwatch/web/templates/inbox/list.html` — replace the bare `<h1>` line with a flex header carrying the button.

**Test files extended:**
- `tests/unit/test_inbox_service.py` — two new tests for `mark_all_seen`.
- `tests/integration/test_inbox_view.py` — one new test for the endpoint + redirect.

---

### Task 1: `InboxService.mark_all_seen()` bulk method

**Files:**
- Modify: `regwatch/services/inbox.py`
- Modify: `tests/unit/test_inbox_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_inbox_service.py`:

```python
def test_mark_all_seen_marks_every_new_event(tmp_path: Path) -> None:
    session = _session(tmp_path)
    e1 = _add_event(
        session, severity="CRITICAL", review_status="NEW", content_hash="n1"
    )
    e2 = _add_event(
        session, severity="MATERIAL", review_status="NEW", content_hash="n2"
    )
    e3 = _add_event(
        session, severity="INFORMATIONAL", review_status="NEW", content_hash="n3"
    )
    seen = _add_event(
        session, severity="MATERIAL", review_status="SEEN", content_hash="s1"
    )
    archived = _add_event(
        session, severity="MATERIAL", review_status="ARCHIVED", content_hash="a1"
    )
    session.commit()

    svc = InboxService(session)
    count = svc.mark_all_seen()
    session.commit()

    assert count == 3
    assert svc.count_new() == 0

    # Previously-NEW rows are now SEEN with seen_at set.
    for ev_id in (e1.event_id, e2.event_id, e3.event_id):
        ev = session.get(UpdateEvent, ev_id)
        assert ev is not None
        assert ev.review_status == "SEEN"
        assert ev.seen_at is not None

    # Previously SEEN/ARCHIVED rows are untouched.
    seen_after = session.get(UpdateEvent, seen.event_id)
    archived_after = session.get(UpdateEvent, archived.event_id)
    assert seen_after is not None and seen_after.review_status == "SEEN"
    assert archived_after is not None and archived_after.review_status == "ARCHIVED"


def test_mark_all_seen_returns_zero_when_inbox_empty(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add_event(
        session, severity="MATERIAL", review_status="SEEN", content_hash="s",
    )
    session.commit()

    svc = InboxService(session)
    count = svc.mark_all_seen()
    session.commit()

    assert count == 0
```

- [ ] **Step 2: Run tests, confirm fail**

Activate venv: `. .venv/Scripts/activate`
Run: `pytest tests/unit/test_inbox_service.py -v -k mark_all_seen`
Expected: FAIL with `AttributeError: 'InboxService' object has no attribute 'mark_all_seen'`.

- [ ] **Step 3: Add `mark_all_seen` to `InboxService`**

In `regwatch/services/inbox.py`, change the SQLAlchemy import line. Currently:

```python
from sqlalchemy import case, desc
```

Replace with:

```python
from sqlalchemy import case, desc, update
```

Add the new method INSIDE the `InboxService` class, immediately after `archive`:

```python
    def mark_all_seen(self) -> int:
        """Bulk-mark every NEW event as SEEN with seen_at=now().

        Returns the number of rows updated. Operates on all NEW events
        regardless of any UI filter — the route is invoked from a header
        button that means "drain my inbox".
        """
        now = datetime.now(UTC)
        result = self._session.execute(
            update(UpdateEvent)
            .where(UpdateEvent.review_status == "NEW")
            .values(review_status="SEEN", seen_at=now)
        )
        return int(result.rowcount or 0)
```

(`datetime` and `UTC` are already imported at the top of the file.)

- [ ] **Step 4: Run tests, confirm pass**

Run: `pytest tests/unit/test_inbox_service.py -v`
Expected: ALL existing inbox-service tests + the 2 new ones PASS.

- [ ] **Step 5: Run lint and type-check**

Run: `ruff check regwatch/services/inbox.py && mypy regwatch/services/inbox.py`
Expected: ruff exits 0. mypy may show pre-existing patterns; report only NEW errors.

- [ ] **Step 6: Commit**

```bash
git add regwatch/services/inbox.py tests/unit/test_inbox_service.py
git commit -m "feat(services): InboxService.mark_all_seen bulk-updates NEW events to SEEN"
```

---

### Task 2: `POST /inbox/mark-all-seen` route

**Files:**
- Modify: `regwatch/web/routes/inbox.py`
- Modify: `tests/integration/test_inbox_view.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_inbox_view.py`:

```python
def test_mark_all_seen_endpoint_clears_inbox_and_redirects(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_event(tmp_path / "app.db", title="A", content_hash="a" * 64)
    _seed_event(tmp_path / "app.db", title="B", content_hash="b" * 64)

    # Pre-condition: both events visible.
    pre = client.get("/inbox")
    assert "Inbox (2 new)" in pre.text

    # POST without following the redirect.
    resp = client.post("/inbox/mark-all-seen", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/inbox"

    # After the redirect target loads, the inbox is empty.
    follow = client.get("/inbox")
    assert "Inbox (0 new)" in follow.text

    # DB rows actually changed to SEEN.
    engine = create_app_engine(tmp_path / "app.db")
    with Session(engine) as session:
        rows = session.query(UpdateEvent).all()
        assert len(rows) == 2
        for ev in rows:
            assert ev.review_status == "SEEN"
            assert ev.seen_at is not None
```

- [ ] **Step 2: Run test, confirm fail**

Run: `pytest tests/integration/test_inbox_view.py::test_mark_all_seen_endpoint_clears_inbox_and_redirects -v`
Expected: FAIL with 405 (Method Not Allowed) — the route doesn't exist.

- [ ] **Step 3: Add the endpoint**

In `regwatch/web/routes/inbox.py`, change the imports near the top. Currently:

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
```

Replace with:

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
```

Add the new route AFTER `inbox_list` and BEFORE `mark_seen`:

```python
@router.post("/mark-all-seen")
def mark_all_seen(request: Request) -> RedirectResponse:
    """Mark every NEW event as SEEN, then redirect back to the inbox.

    Always operates on the full set of NEW events; UI filters are
    intentionally ignored.
    """
    with request.app.state.session_factory() as session:
        InboxService(session).mark_all_seen()
        session.commit()
    return RedirectResponse(url="/inbox", status_code=303)
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `pytest tests/integration/test_inbox_view.py -v`
Expected: ALL inbox-view tests + the new one PASS.

- [ ] **Step 5: Run lint and type-check**

Run: `ruff check regwatch/web/routes/inbox.py && mypy regwatch/web/routes/inbox.py`
Expected: ruff exits 0. Pre-existing mypy patterns acceptable.

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/routes/inbox.py tests/integration/test_inbox_view.py
git commit -m "feat(web): POST /inbox/mark-all-seen drains inbox in one click"
```

---

### Task 3: "Mark all as seen" button on `inbox/list.html`

**Files:**
- Modify: `regwatch/web/templates/inbox/list.html`

This task is a pure template edit. Verification is via the existing integration tests + the new one from Task 2 (which checks the page renders correctly before and after the action).

- [ ] **Step 1: Replace the bare `<h1>` line with a flex header containing the button**

In `regwatch/web/templates/inbox/list.html`, replace this line:

```html
  <h1 class="text-2xl font-bold mb-4">Inbox ({{ events|length }} new)</h1>
```

with:

```html
  <div class="flex items-center justify-between mb-4">
    <h1 class="text-2xl font-bold">Inbox ({{ events|length }} new)</h1>
    {% if events %}
      <form method="post" action="/inbox/mark-all-seen">
        <button class="px-3 py-1 bg-slate-700 text-white rounded text-xs hover:bg-slate-800">
          Mark all as seen
        </button>
      </form>
    {% endif %}
  </div>
```

The rest of the file is unchanged.

- [ ] **Step 2: Run inbox view tests + smoke**

Run: `pytest tests/integration/test_inbox_view.py tests/integration/test_app_smoke.py -v`
Expected: ALL PASS — no Jinja errors.

- [ ] **Step 3: Commit**

```bash
git add regwatch/web/templates/inbox/list.html
git commit -m "feat(web): inbox shows 'Mark all as seen' header button"
```

---

### Task 4: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `pytest`
Expected: ALL PASS. Should be ~519 (previous baseline ~516 + 3 new).

- [ ] **Step 2: Run lint**

Run: `ruff check regwatch`
Expected: clean.

- [ ] **Step 3: Manual UI check**

```bash
uvicorn regwatch.main:app --reload
```

Open http://localhost:8001/inbox. With NEW events present:
1. The "Mark all as seen" button appears at the top right of the inbox header.
2. Clicking it returns to /inbox showing "Inbox (0 new)" and "No new updates."
3. The sidebar Inbox badge drops to 0 on the same render (since `_count_inbox` queries `review_status='NEW'`).

If no NEW events are present, the button is not rendered.

- [ ] **Step 4: No commit needed.**

---

## Verification checklist

- [ ] `mark_all_seen()` returns the correct count (Task 1).
- [ ] Previously SEEN / ARCHIVED rows are not touched (Task 1).
- [ ] `POST /inbox/mark-all-seen` returns 303 and redirects to `/inbox` (Task 2).
- [ ] Sidebar Inbox badge drops to 0 after the bulk operation (Task 4 manual; covered indirectly because `_count_inbox` reads `review_status='NEW'` and the redirect-target render reads it fresh).
- [ ] Button is hidden when no NEW events exist (Task 3 + 4 manual).

## Out of scope

- Filter-aware bulk marking.
- Confirmation dialog.
- "Mark all as archived" companion.
- An undo button.
- Optimistic UI / HTMX swap (a 303 redirect is enough for this single-user tool).
