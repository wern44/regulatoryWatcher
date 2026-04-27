# Inbox "Mark All as Seen" Button — Design Spec

**Date:** 2026-04-27
**Status:** Approved

## Goal

Give the user a one-click way to mark every `update_event` with `review_status='NEW'` as `SEEN`, so they can drain the inbox without clicking each row's existing per-row "mark seen" button. Operates on **all** NEW events regardless of any active filters in the inbox UI (source, entity_type, show_all) — that's the explicit user choice.

## Non-goals

- Filter-aware bulk marking (deferred; full-clear is what was asked for).
- A confirmation dialog. The action is reversible per-row through the database, and this is a single-user local tool.
- A "mark all as archived" companion. Archive remains a per-row action.
- Undoing a bulk operation in one click.

## 1. Service — new method on `InboxService`

In `regwatch/services/inbox.py`, add:

```python
def mark_all_seen(self) -> int:
    """Mark every NEW event as SEEN with seen_at=now(). Returns count updated."""
    now = datetime.now(UTC)
    result = self._session.execute(
        update(UpdateEvent)
        .where(UpdateEvent.review_status == "NEW")
        .values(review_status="SEEN", seen_at=now)
    )
    return int(result.rowcount or 0)
```

Uses a single bulk `UPDATE` instead of loading every row into the ORM. Faster and far less write contention than per-row updates (relevant given the unrelated DB-locked issue currently being investigated separately).

The required imports (`update`, `datetime`, `UTC`) need adding to the top of the file.

## 2. Route — `POST /inbox/mark-all-seen`

In `regwatch/web/routes/inbox.py`, add a new route between `inbox_list` and the existing `mark_seen` POST:

```python
@router.post("/mark-all-seen")
def mark_all_seen(request: Request) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        InboxService(session).mark_all_seen()
        session.commit()
    return RedirectResponse(url="/inbox", status_code=303)
```

The `/inbox` prefix on the router gives the full path `/inbox/mark-all-seen`. The 303 makes the browser GET `/inbox` after the POST, landing the user on the freshly-emptied inbox (and the sidebar Inbox badge updates because `_count_inbox` queries `review_status='NEW'`).

A new import for `RedirectResponse` is needed if not already present.

## 3. UI — header button on `inbox/list.html`

Wrap the existing `<h1>` in a flex container and add the button. Render the button only when there is at least one NEW event (which is always true while `events|length > 0` because the inbox lists `review_status='NEW'`). New header block:

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

The form is a tiny standalone POST — no HTMX, no JS, no CSRF token (this codebase doesn't use one for any POST today).

## 4. Tests

### Unit — `tests/unit/test_inbox_service.py`

Two new tests:

```python
def test_mark_all_seen_marks_every_new_event(...):
    # Seed: 3 NEW, 1 SEEN, 1 ARCHIVED
    # Call: count = InboxService(session).mark_all_seen(); session.commit()
    # Assert: count == 3
    # Assert: NEW count is now 0
    # Assert: previously SEEN row stays SEEN, previously ARCHIVED stays ARCHIVED
    # Assert: each newly-SEEN row has seen_at set


def test_mark_all_seen_returns_zero_when_inbox_empty(...):
    # No NEW events
    # Call: count = InboxService(session).mark_all_seen()
    # Assert: count == 0
```

### Integration — `tests/integration/test_inbox_view.py`

One new test:

```python
def test_mark_all_seen_endpoint_clears_inbox_and_redirects(...):
    # Seed two NEW events via session_factory.
    # POST /inbox/mark-all-seen, follow_redirects=False
    # Assert: status 303 with Location header /inbox
    # Follow up GET /inbox; assert HTML contains "Inbox (0 new)"
```

## 5. Edge cases handled by the design

| Case | Behavior |
|---|---|
| Empty inbox | Button isn't rendered (`{% if events %}`). If a user POSTs the URL directly anyway, `mark_all_seen()` returns 0, redirect still goes to `/inbox`. |
| Concurrent pipeline ingest | The bulk `UPDATE` is one fast statement; no transaction held for the rest of the request. |
| User filter active when clicking | The action ignores filters and marks every NEW event. The user explicitly chose this (option B during brainstorming). |
| Sidebar Inbox badge | Drops to 0 automatically because `SidebarBadgeService._count_inbox` queries `review_status='NEW'`. |

## Files changed

| File | Change |
|---|---|
| `regwatch/services/inbox.py` | New `mark_all_seen` method; `update` and `datetime`/`UTC` imports if not present. |
| `regwatch/web/routes/inbox.py` | New `POST /mark-all-seen` route returning `RedirectResponse`. |
| `regwatch/web/templates/inbox/list.html` | Replace `<h1>` line with flex header + button form. |
| `tests/unit/test_inbox_service.py` | Two new tests. |
| `tests/integration/test_inbox_view.py` | One new test. |
