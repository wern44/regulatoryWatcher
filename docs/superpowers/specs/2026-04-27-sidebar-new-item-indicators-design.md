# Sidebar New-Item Indicators — Design Spec

**Date:** 2026-04-27
**Status:** Approved

## Goal

Show a small numeric badge next to each major sidebar entry — Inbox, Catalog, ICT / DORA, Drafts, Deadlines — that tells the user how many items have been added to that section since they last visited it. Visiting a section's page clears its own badge.

## Non-goals

- Per-item read tracking. The Inbox already has `review_status: NEW / SEEN / ARCHIVED` for triage; that mechanism is independent of the sidebar badge.
- Real-time push updates. The badge is recomputed at page render time. A normal page reload is enough.
- Badges on Dashboard, Q&A, Settings, or sidebar sub-entries (`AIFM`, `Chapter 15 ManCo`, `Extraction Fields`, `Schedules`) — only the five top-level pages above.
- Tracking *transitions* into a section (e.g., a regulation becoming `is_ict=True` later, or filling in a `transposition_deadline` after it was added). "New" only means "the row was created since the user's last visit to that section."

## Architecture overview

Each section gets one row in the existing `setting` key-value table:

| Setting key | Value |
|---|---|
| `last_visit_inbox` | ISO-8601 UTC timestamp |
| `last_visit_catalog` | ISO-8601 UTC timestamp |
| `last_visit_ict` | ISO-8601 UTC timestamp |
| `last_visit_drafts` | ISO-8601 UTC timestamp |
| `last_visit_deadlines` | ISO-8601 UTC timestamp |

A new service, `SidebarBadgeService` (`regwatch/services/sidebar_badges.py`), exposes one method:

```python
@dataclass
class SidebarBadges:
    inbox: int
    catalog: int
    ict: int
    drafts: int
    deadlines: int

class SidebarBadgeService:
    def __init__(self, session: Session) -> None: ...
    def counts(self) -> SidebarBadges: ...
    def mark_visited(self, section: str) -> None: ...
```

`counts()` runs five short `SELECT count(*)` queries — each filtered by the section's "newness" predicate AND the section's last-visit timestamp from the `setting` table.

`mark_visited(section)` is called from the section's GET route at the **end** of the handler, AFTER the data has been read for the response. This guarantees the user sees the badge they expected on the page they just opened — clearing happens for the *next* page load.

A FastAPI dependency / Jinja context processor injects a `SidebarBadges` instance into every template's render context as `sidebar_badges`. The sidebar partial reads from it.

## 1. Counting queries

| Section | Query (pseudo-SQL) |
|---|---|
| Inbox | `SELECT count(*) FROM update_event WHERE fetched_at > :last_visit_inbox` |
| Catalog | `SELECT count(*) FROM regulation WHERE created_at > :last_visit_catalog` |
| ICT/DORA | `SELECT count(*) FROM regulation WHERE created_at > :last_visit_ict AND is_ict = true` |
| Drafts | `SELECT count(*) FROM regulation WHERE created_at > :last_visit_drafts AND lifecycle_stage IN ('CONSULTATION','PROPOSAL','DRAFT_BILL','ADOPTED_NOT_IN_FORCE')` |
| Deadlines | `SELECT count(*) FROM regulation WHERE created_at > :last_visit_deadlines AND (transposition_deadline IS NOT NULL OR application_date IS NOT NULL)` |

The Deadlines query is intentionally simple: any regulation that was newly added AND has at least one deadline date set. We do not inspect whether the deadline date is in the past, future, or has been dismissed — keeping it consistent with the other "row was added" predicates.

The badge is capped at display-time as `99+` for any value `>= 100`. The query returns the real count; the cap is a template concern.

## 2. Schema change — `regulation.created_at`

`Regulation` does not currently have a `created_at` column. Add one:

```python
created_at: Mapped[datetime] = mapped_column(
    TZDateTime, default=lambda: datetime.now(UTC), index=True
)
```

`Base.metadata.create_all` will add it automatically for fresh databases. For existing databases we add a tiny migration in `regwatch/db/migrations.py`:

```python
def migrate_regulation_created_at(engine: Engine) -> None:
    """Add regulation.created_at, backfilling existing rows to the migration time.

    Idempotent. Backfilling to NOW() at migration time means that no
    pre-existing regulation will ever count as 'new' once the user has
    visited the relevant section once.
    """
    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(regulation)"))]
        if not cols:
            return  # fresh DB; create_all handles it
        if "created_at" in cols:
            return  # already migrated
        now_iso = datetime.now(UTC).isoformat()
        conn.execute(text("ALTER TABLE regulation ADD COLUMN created_at DATETIME"))
        result = conn.execute(
            text("UPDATE regulation SET created_at = :ts WHERE created_at IS NULL"),
            {"ts": now_iso},
        )
        logger.info(
            "Backfilled regulation.created_at on %d existing rows", result.rowcount
        )
```

Wired into `regwatch/db/engine.py` next to the existing migration calls.

## 3. Visit-clearing semantics

When the user opens `/inbox`, `/catalog`, `/ict`, `/drafts`, or `/deadlines`:

1. The route reads the section's data and renders the response (existing behaviour). At this render the sidebar still sees the OLD `last_visit_<section>` value, so the badge that was visible on the previous page is still visible on the page the user just opened — the user gets the satisfying confirmation that their attention was warranted.
2. **At the end of the handler, after the response context is built but before returning**, the route calls `SidebarBadgeService.mark_visited("<section>")` with the value `datetime.now(UTC)` and commits. The next render anywhere in the app will read the new timestamp and show 0.

Default for missing setting: if `last_visit_<section>` is absent from the `setting` table, `SidebarBadgeService.counts()` treats it as `datetime.now(UTC)` — i.e., "user has just visited everything". This means a freshly migrated deploy shows zero badges until the next item is created. The setting is created lazily — `mark_visited` does an upsert.

Edge case — items added during the request: in this single-user tool the only writer is the pipeline thread, which is exclusive with the request thread when running, so there is no realistic race where an item lands between the sidebar render and `mark_visited`. We accept the theoretical microsecond window rather than capture-and-restore around the timestamp.

## 4. UI

In `regwatch/web/templates/partials/sidebar.html`, each top-level link gets a trailing `<span>` rendered only when the count is non-zero:

```html
<a href="/inbox" class="...flex items-center justify-between...">
  <span>Inbox</span>
  {% if sidebar_badges.inbox %}
    <span class="ml-2 inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5
                 bg-amber-500 text-white text-xs font-semibold rounded-full">
      {{ sidebar_badges.inbox if sidebar_badges.inbox < 100 else '99+' }}
    </span>
  {% endif %}
</a>
```

- **Right-aligned** via `flex items-center justify-between`.
- **Hidden when zero** — no empty pill, no `0`.
- **Capped at "99+"** for `>= 100`.
- **Amber** (`bg-amber-500 text-white`) — informational, not urgent. Same family as the new "Aborted" treatment.
- **No icon, no animation, no tooltip** — just the number.

Sub-entries (`AIFM`, `Chapter 15 ManCo`, `Extraction Fields`, `Schedules`), the Dashboard link, the Q&A link, and the Settings parent link get **no badge**. They're filtered views or pages where the concept doesn't apply.

## 5. Template wiring

The sidebar reads `sidebar_badges` from the render context. We pick one mechanism — a thin `render_page` wrapper around `TemplateResponse` — and use it everywhere a sidebar-bearing page is rendered. We deliberately do NOT add a Jinja2 global that pulls a session at template-render time, because that hides DB I/O behind template rendering and breaks test isolation.

New file `regwatch/web/templates_context.py`:

```python
"""Shared render helper that auto-injects sidebar context."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from regwatch.services.sidebar_badges import SidebarBadgeService


def render_page(
    request: Request, template_name: str, context: dict[str, Any]
) -> HTMLResponse:
    """Render a full page (extends base.html). Auto-injects sidebar_badges.

    Use this instead of templates.TemplateResponse for any view that
    extends base.html. Partials and HTMX fragments should keep using
    templates.TemplateResponse directly.
    """
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        badges = SidebarBadgeService(session).counts()
    return templates.TemplateResponse(
        request, template_name, {**context, "sidebar_badges": badges}
    )
```

Each page-rendering route (those whose template extends `base.html`) changes `templates.TemplateResponse(request, "x.html", {...})` to `render_page(request, "x.html", {...})`. The five "clearing" routes additionally call `SidebarBadgeService(session).mark_visited("<section>")` before returning.

Page-rendering routes that need this change:

- `regwatch/web/routes/dashboard.py::dashboard_view`
- `regwatch/web/routes/inbox.py::inbox_list` (also clears)
- `regwatch/web/routes/catalog.py::catalog_view` (also clears)
- `regwatch/web/routes/ict.py::ict_list` (also clears)
- `regwatch/web/routes/drafts.py::drafts` (also clears)
- `regwatch/web/routes/deadlines.py::deadlines` (also clears)
- `regwatch/web/routes/regulation_detail.py::regulation_detail`
- `regwatch/web/routes/chat.py::chat_list`, `chat_session`, `chat_new`
- `regwatch/web/routes/settings.py::settings_view`
- `regwatch/web/routes/schedules.py::schedules_view`
- `regwatch/web/routes/analysis.py::analysis_run`
- `regwatch/web/routes/discovery.py::discovery_list`, `discovery_run`
- Any other route returning a template that `extends "base.html"`

Partials, HTMX fragment endpoints (`/run-pipeline/status`, `/run-pipeline/abort`, `/status-bar`, `/inbox/{id}/mark-seen`, etc.) keep `templates.TemplateResponse` because their fragments do not include the sidebar.

Implementation order (planned in the next-stage plan): write `render_page` and the badge service first, then convert the routes one by one starting with the five clearing routes, then the rest.

## 6. Files changed

| File | Change |
|---|---|
| `regwatch/db/models.py` | Add `created_at` to `Regulation`. |
| `regwatch/db/migrations.py` | Add `migrate_regulation_created_at`. |
| `regwatch/db/engine.py` | Call the new migration. |
| `regwatch/services/sidebar_badges.py` (new) | `SidebarBadgeService` + `SidebarBadges` DTO. |
| `regwatch/main.py` | Register Jinja global `sidebar_badges_for(request)`. |
| `regwatch/web/templates/partials/sidebar.html` | Render `{% if sidebar_badges.x %}` pill on each of the 5 entries. |
| `regwatch/web/routes/inbox.py` | `mark_visited("inbox")` in `inbox_list` GET. |
| `regwatch/web/routes/catalog.py` | `mark_visited("catalog")` in catalog GET. |
| `regwatch/web/routes/ict.py` | `mark_visited("ict")` in ict GET. |
| `regwatch/web/routes/drafts.py` | `mark_visited("drafts")` in drafts GET. |
| `regwatch/web/routes/deadlines.py` | `mark_visited("deadlines")` in deadlines GET. |
| `tests/unit/test_sidebar_badges.py` (new) | Counts vs. timestamps; mark_visited upserts. |
| `tests/integration/test_sidebar_badges.py` (new) | Visit /inbox → next render shows 0 for inbox; counts unchanged for other sections. |
| `tests/unit/test_db_migrations.py` (extend if exists, else new) | Idempotent migration adds column and backfills. |

## 7. Testing strategy

**Unit:** `tests/unit/test_sidebar_badges.py`

- Construct `SidebarBadgeService` against a fresh in-memory SQLite. Insert 3 `update_event` rows with `fetched_at = NOW`, set `last_visit_inbox` to a past timestamp, assert `counts().inbox == 3`. Move `last_visit_inbox` to a later timestamp; assert `counts().inbox == 0`.
- Repeat for catalog/ict/drafts/deadlines using `Regulation.created_at` and the section-specific filters.
- Test `mark_visited("inbox")` upserts the setting key.
- Test missing setting key returns 0 counts (default = "treat as visited now").

**Integration:** `tests/integration/test_sidebar_badges.py`

- Spin up `_client(tmp_path, monkeypatch)` (the standard test app helper). Insert one regulation with `is_ict=True` and `created_at = NOW`. Render any page → assert sidebar HTML contains `>1<` near the ICT / DORA link. GET `/ict` → render again → assert `>1<` no longer appears (badge cleared).
- Verify Dashboard and Q&A links never show a badge regardless of state.

**Migration test:**

- Pre-create a SQLite DB with the old `regulation` schema (no `created_at`), run the migration, assert column exists and all existing rows have a non-null timestamp.

## Out of scope

- Tracking deadline-date changes on existing regulations (definition B/C from brainstorming).
- Real-time updates without page reload.
- Badges on sub-entries.
- Per-user state (this is a single-user tool).
- Notifying the user via email/desktop notifications.
