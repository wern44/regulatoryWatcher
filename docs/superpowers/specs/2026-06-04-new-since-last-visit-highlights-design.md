# "New since last visit" Row Highlights — Design Spec

**Date:** 2026-06-04
**Status:** Approved

## Goal

When the sidebar shows a "new items" badge for Catalog, ICT/DORA, Drafts, or Deadlines, opening that page should make it visually obvious *which rows* contributed to the badge. Today the badge clears the moment the page loads (`SidebarBadgeService.mark_visited` is called at the end of every section's GET handler) but nothing on the page distinguishes the new rows from the old ones, so the user has to scan the entire list trying to guess.

The highlight applies on the same render that clears the badge. On the next visit the cutoff has already advanced, so nothing is highlighted — which matches the badge being back to zero.

## Non-goals

- **Inbox** is out of scope. Its badge counts `update_event` rows where `review_status = 'NEW'`, not a timestamp; new events are already visually distinct via the existing inbox UI.
- **No new toggle or filter mode.** Highlight is purely a visual treatment on rows the page would already display.
- **No persistence of "previously seen as new".** Once the cutoff is advanced (i.e. the next visit), highlights disappear. There is no "show me again what was new yesterday".
- **No real-time updates.** A page reload is enough; the highlight is computed at render time from the row list and the previous cutoff.

## Architecture overview

The change is local: one service signature tweak, four routes wired up symmetrically, two DTO extensions, three templates updated. No new tables, no new endpoints, no JS.

### Service change — return the previous cutoff atomically

`SidebarBadgeService.mark_visited` becomes:

```python
def mark_visited(self, section: str) -> datetime | None:
    """Upsert last_visit_<section> = now; return the previous value (or None)."""
```

The previous value is the cutoff the badge was just computed against. Returning it from the same call that overwrites it keeps the read-and-write atomic per session, so the route doesn't have to coordinate the two halves itself.

All four existing call sites discard the current `None` return — adding a return value is non-breaking.

### Route change — compute `new_ids` from the row list already in scope

Each of the four routes (`catalog`, `ict`, `drafts`, `deadlines`) does the same thing right before rendering:

```python
previous_cutoff = SidebarBadgeService(session).mark_visited(SECTION)
new_ids: set[int] = (
    {r.regulation_id for r in regs if r.created_at > previous_cutoff}
    if previous_cutoff is not None else set()
)
```

`new_ids` is passed into the template context. Computing the set in Python from the already-fetched row list (rather than a second SQL query) keeps the highlight set automatically consistent with whatever filters the page applied — there's no risk of highlighting a row that isn't on screen.

For the deadlines page, the comparison is against the deadline DTO's regulation creation timestamp (see DTO change below); one regulation can produce two deadline rows (TRANSPOSITION + APPLICATION), and the highlight should apply to both if the regulation itself is new.

### DTO changes — expose `created_at`

- `RegulationDTO` (used by Catalog / ICT / Drafts): add `created_at: datetime`, populated from `Regulation.created_at` in `_to_dto`.
- `DeadlineDTO` (used by Deadlines): add `regulation_created_at: datetime`, populated from `Regulation.created_at` in `DeadlineService.upcoming`.

No other DTO fields move or change.

### Template changes — row tint + NEW pill

| Template | Used by | Change |
|---|---|---|
| `partials/catalog_row.html` | Catalog, Drafts | Add `bg-amber-50` to the `<tr>` class when `r.regulation_id in new_ids`. Render a small amber `NEW` pill (using the existing `bg-amber-100 text-amber-800` style used by the "+N amendments" pill) inside the reference cell, immediately after the reference-number link and *before* the optional amendments pill. |
| `ict/list.html` | ICT/DORA | Same `bg-amber-50` and `NEW` pill inline on the row markup. Pill goes inside the reference `<td>`, after the reference-number text. The existing `bg-amber-50` for `r.needs_review` uses the same shade; when both conditions apply, the row tint stays the same and the `NEW` pill disambiguates. |
| `deadlines/list.html` | Deadlines | Same `bg-amber-50` and `NEW` pill on each `<tr>`. Pill goes inside the reference `<td>`, after the reference-number text. The existing `opacity-50` for `d.done` items layers correctly with the tint. |

`new_ids` defaults to an empty set, so all templates render identically to today when nothing qualifies as new (first-ever visit, or no new items since last visit).

## Semantics — what "new" means

- "New" = `Regulation.created_at > previous_cutoff`.
- First-ever visit to a section: `previous_cutoff is None`, `new_ids` is empty, nothing highlighted (matches the badge, which also shows 0 in this case per `SidebarBadgeService._count_*`).
- Subsequent visit: rows that triggered the badge get highlighted on this render. On the next visit, the cutoff has advanced past `now()` of the prior visit, so those rows no longer qualify.
- The highlight set on each page mirrors the page's own filters — e.g. a row that's new but doesn't match the active entity-type cookie isn't displayed and therefore isn't highlighted either.

## Testing

Per project conventions, integration-level tests using the standard `_client` fixture pattern from `tests/integration/test_app_smoke.py`:

- **Unit:** `SidebarBadgeService.mark_visited` returns the previous timestamp on second call; returns `None` on first call; the stored value advances each call.
- **Integration, per section (Catalog, ICT, Drafts, Deadlines):**
  - Seed a `Regulation` with `created_at` before a manually-set `last_visit_<section>` and another with `created_at` after.
  - GET the page.
  - Assert the response HTML contains the `NEW` pill (and `bg-amber-50` class) on the new row's reference number, and does *not* contain them on the old row's.
  - Assert the `last_visit_<section>` Setting was updated to a time later than the previous value.
- **Integration, no-cutoff path:** with no `last_visit_<section>` setting, GET the page, assert no row carries the `NEW` marker even when there are recent regulations.
- **Integration, second-visit clear:** GET the page twice; on the second response, no row carries the `NEW` marker even though they did on the first response.

## Out of scope cleanup

None. The existing module structure already isolates badge logic in one service and the per-section data fetch in one route per section. No surrounding refactor is justified by this change.
