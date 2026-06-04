# "New since last visit" Row Highlights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Highlight Catalog / ICT / Drafts / Deadlines rows that contributed to the sidebar "new items" badge on the same render that clears it, so the user can immediately see which entries are new.

**Architecture:** `SidebarBadgeService.mark_visited` returns the previous `last_visit_<section>` timestamp atomically. Each section route compares its already-fetched row list against that cutoff to build a `new_ids: set[int]` set, which is passed into the template context. The row partials apply `bg-amber-50` and a small `NEW` pill when `r.regulation_id in new_ids`.

**Tech Stack:** Python 3.12 (FastAPI + SQLAlchemy + Jinja2 + HTMX + Tailwind CSS), pytest.

**Spec:** `docs/superpowers/specs/2026-06-04-new-since-last-visit-highlights-design.md`

---

## File Structure

**Modify:**

- `regwatch/services/sidebar_badges.py` — `mark_visited` returns `datetime | None`.
- `regwatch/services/regulations.py` — `RegulationDTO` gains `created_at: datetime`; `_to_dto` populates it.
- `regwatch/services/deadlines.py` — `DeadlineDTO` gains `regulation_created_at: datetime`; `upcoming` populates it.
- `regwatch/web/routes/catalog.py` — capture previous cutoff, compute `new_ids`, pass into template context.
- `regwatch/web/routes/ict.py` — same pattern.
- `regwatch/web/routes/drafts.py` — same pattern.
- `regwatch/web/routes/deadlines.py` — same pattern.
- `regwatch/web/templates/partials/catalog_row.html` — apply row tint + `NEW` pill when row is in `new_ids`. (Shared by Catalog and Drafts.)
- `regwatch/web/templates/ict/list.html` — apply row tint + `NEW` pill on the inline `<tr>`.
- `regwatch/web/templates/deadlines/list.html` — apply row tint + `NEW` pill on the inline `<tr>`.

**Tests touched / added:**

- `tests/unit/test_sidebar_badges.py` — add test for `mark_visited` return value.
- `tests/integration/test_new_since_last_visit_highlights.py` — **new** file, one test per section.

---

### Task 1: `mark_visited` returns the previous timestamp

**Files:**
- Modify: `regwatch/services/sidebar_badges.py:60-71`
- Test: `tests/unit/test_sidebar_badges.py`

- [ ] **Step 1: Add the failing unit test**

Append to `tests/unit/test_sidebar_badges.py` (after `test_mark_visited_rejects_unknown_section`):

```python
def test_mark_visited_returns_previous_timestamp(tmp_path):
    """mark_visited() returns the prior last_visit_<section> value (or None)
    and overwrites the stored value with `now`. The route uses the returned
    value as the cutoff for highlighting `new` rows on this same render."""
    session = _session(tmp_path)
    svc = SidebarBadgeService(session)

    # First call: no prior value -> returns None, stores now.
    previous = svc.mark_visited("catalog")
    session.commit()
    assert previous is None
    stored1 = datetime.fromisoformat(
        session.get(Setting, "last_visit_catalog").value
    )
    if stored1.tzinfo is None:
        stored1 = stored1.replace(tzinfo=UTC)

    # Second call: returns the previously-stored timestamp, advances the value.
    previous2 = svc.mark_visited("catalog")
    session.commit()
    assert previous2 is not None
    assert previous2 == stored1
    stored2 = datetime.fromisoformat(
        session.get(Setting, "last_visit_catalog").value
    )
    if stored2.tzinfo is None:
        stored2 = stored2.replace(tzinfo=UTC)
    assert stored2 >= stored1
```

- [ ] **Step 2: Run the test, verify it fails**

```powershell
. .venv/Scripts/activate
pytest tests/unit/test_sidebar_badges.py::test_mark_visited_returns_previous_timestamp -v
```

Expected: FAIL with `assert None is not None` (current `mark_visited` returns `None`).

- [ ] **Step 3: Update `mark_visited` to return the previous value**

Edit `regwatch/services/sidebar_badges.py:60-71` — replace the existing method:

```python
    def mark_visited(self, section: str) -> datetime | None:
        """Upsert last_visit_<section> = now; return the previous value (or None).

        The previous value is the cutoff the badge was just computed against,
        so callers can use it to highlight rows that are 'new since last visit'
        on the same render that clears the badge.
        """
        if section not in SECTION_KEYS:
            raise ValueError(f"unknown section: {section!r}")
        key = SECTION_KEYS[section]
        now = datetime.now(UTC)
        existing = self._session.get(Setting, key)
        if existing is None:
            self._session.add(Setting(key=key, value=now.isoformat(), updated_at=now))
            return None
        previous = datetime.fromisoformat(existing.value)
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        existing.value = now.isoformat()
        existing.updated_at = now
        return previous
```

- [ ] **Step 4: Run the new test + the full sidebar_badges unit test file**

```powershell
pytest tests/unit/test_sidebar_badges.py -v
```

Expected: every test passes, including the new `test_mark_visited_returns_previous_timestamp`.

- [ ] **Step 5: Commit**

```powershell
git add regwatch/services/sidebar_badges.py tests/unit/test_sidebar_badges.py
git commit -m "feat(sidebar): mark_visited returns previous timestamp"
```

---

### Task 2: `RegulationDTO` exposes `created_at`

**Files:**
- Modify: `regwatch/services/regulations.py:27-41, 87-101`
- Test: integration suites already cover catalog/ict/drafts — Task 5+ adds the new behavioral tests.

- [ ] **Step 1: Add `created_at` to `RegulationDTO`**

Edit `regwatch/services/regulations.py` — at the top of the file, ensure `datetime` is imported alongside `date`. Replace the existing single import line:

```python
from datetime import date
```

with:

```python
from datetime import date, datetime
```

Add the field to `RegulationDTO` (between `dora_pillar` and the end of the dataclass):

```python
@dataclass
class RegulationDTO:
    regulation_id: int
    reference_number: str
    title: str
    type: str
    issuing_authority: str
    lifecycle_stage: str
    is_ict: bool
    url: str
    transposition_deadline: date | None
    application_date: date | None
    needs_review: bool
    dora_pillar: str | None
    created_at: datetime
```

Update `_to_dto`:

```python
def _to_dto(r: Regulation) -> RegulationDTO:
    return RegulationDTO(
        regulation_id=r.regulation_id,
        reference_number=r.reference_number,
        title=r.title,
        type=r.type.value,
        issuing_authority=r.issuing_authority,
        lifecycle_stage=r.lifecycle_stage.value,
        is_ict=r.is_ict,
        url=r.url,
        transposition_deadline=r.transposition_deadline,
        application_date=r.application_date,
        needs_review=r.needs_review,
        dora_pillar=r.dora_pillar.value if r.dora_pillar else None,
        created_at=r.created_at,
    )
```

- [ ] **Step 2: Run the existing catalog / drafts / ict tests to confirm no regressions**

```powershell
pytest tests/integration/test_catalog_view.py tests/integration/test_drafts_deadlines_ict_views.py -v
```

Expected: PASS (existing tests don't reference `created_at` so they still pass after the additive field).

- [ ] **Step 3: Commit**

```powershell
git add regwatch/services/regulations.py
git commit -m "feat(regulations): expose created_at on RegulationDTO"
```

---

### Task 3: `DeadlineDTO` exposes `regulation_created_at`

**Files:**
- Modify: `regwatch/services/deadlines.py:1-4, 16-27, 85-99`

- [ ] **Step 1: Import `datetime`**

Edit `regwatch/services/deadlines.py` — change the import at the top:

```python
from datetime import date
```

to:

```python
from datetime import date, datetime
```

- [ ] **Step 2: Add `regulation_created_at` to `DeadlineDTO`**

Replace the existing `DeadlineDTO`:

```python
@dataclass
class DeadlineDTO:
    regulation_id: int
    reference_number: str
    title: str
    kind: DeadlineKind
    due_date: date
    days_until: int
    severity_band: str
    url: str
    done: bool
    regulation_created_at: datetime
```

- [ ] **Step 3: Populate it in `DeadlineService.upcoming`**

In the loop body of `upcoming`, find the `DeadlineDTO(...)` construction and add `regulation_created_at=reg.created_at`:

```python
items.append(
    DeadlineDTO(
        regulation_id=reg.regulation_id,
        reference_number=reg.reference_number,
        title=reg.title,
        kind=kind,  # type: ignore[arg-type]
        due_date=due,
        days_until=days_until,
        severity_band=self.severity_band(days_until),
        url=reg.url,
        done=done_flag,
        regulation_created_at=reg.created_at,
    )
)
```

- [ ] **Step 4: Run the existing deadlines integration test**

```powershell
pytest tests/integration/test_drafts_deadlines_ict_views.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add regwatch/services/deadlines.py
git commit -m "feat(deadlines): expose regulation_created_at on DeadlineDTO"
```

---

### Task 4: Update `partials/catalog_row.html` with row tint + NEW pill

**Files:**
- Modify: `regwatch/web/templates/partials/catalog_row.html`

This partial is shared by `catalog/list.html` and `drafts/list.html`. It will read a `new_ids` variable that may be undefined (e.g., when included from contexts that don't yet pass it). Use `default(set())` to keep behavior backwards-compatible.

- [ ] **Step 1: Replace the partial**

Overwrite `regwatch/web/templates/partials/catalog_row.html` with:

```html
{% set new_ids_set = new_ids|default(none) %}
{% set is_new = new_ids_set is not none and r.regulation_id in new_ids_set %}
<tr class="border-t {% if is_new %}bg-amber-50{% endif %}">
  <td class="p-2">
    <input type="checkbox"
           name="regulation_ids"
           value="{{ r.regulation_id }}"
           onchange="updateActionBar()"
           aria-label="Select {{ r.reference_number }}">
  </td>
  <td class="p-2 font-mono">
    <a class="hover:underline" href="/regulations/{{ r.regulation_id }}">{{ r.reference_number }}</a>
    {% if is_new %}
      <span class="ml-2 px-1.5 py-0.5 bg-amber-100 text-amber-800 text-xs rounded font-semibold"
            title="Added since your last visit">NEW</span>
    {% endif %}
    {% if amendment_counts is defined %}
      {% set n = amendment_counts.get(r.regulation_id, 0) %}
      {% if n %}
        <a href="/regulations/{{ r.regulation_id }}#amendments"
           class="ml-2 px-1.5 py-0.5 bg-amber-100 text-amber-800 text-xs rounded hover:bg-amber-200"
           title="View {{ n }} amendment(s) on this regulation's detail page">
          +{{ n }} amendments
        </a>
      {% endif %}
    {% endif %}
  </td>
  <td class="p-2">{{ r.title }}</td>
  <td class="p-2">{{ r.issuing_authority }}</td>
  <td class="p-2">
    <span class="px-2 py-0.5 rounded text-xs bg-slate-100">{{ r.lifecycle_stage }}</span>
  </td>
  <td class="p-2">{% if r.is_ict %}<span class="text-purple-700 font-semibold">ICT</span>{% endif %}</td>
  {% if status_by_reg is defined %}
  <td class="p-2 text-xs whitespace-nowrap">
    {% set s = status_by_reg.get(r.regulation_id, "never") %}
    {% if s == "ok" %}
      <span class="text-green-700">analysed</span>
    {% elif s == "stale" %}
      <span class="text-amber-700">re-analyse</span>
    {% elif s == "failed" %}
      <span class="text-red-700">failed</span>
    {% else %}
      <span class="text-slate-400">never</span>
    {% endif %}
  </td>
  {% endif %}
</tr>
```

Why `new_ids|default(none)` instead of `default(set())`: Jinja's `|default` with a callable can be brittle across versions, and `none` reads naturally with `is not none`. Either works; this matches Jinja idioms used elsewhere in this codebase (see `sidebar.html`'s `sidebar_badges|default(None)`).

- [ ] **Step 2: Run the existing catalog / drafts smoke tests to confirm rendering still works**

```powershell
pytest tests/integration/test_catalog_view.py tests/integration/test_drafts_deadlines_ict_views.py -v
```

Expected: PASS — `new_ids` is not yet passed by either route, so `is_new` evaluates to `False` for every row and rendering is identical to before.

- [ ] **Step 3: Commit**

```powershell
git add regwatch/web/templates/partials/catalog_row.html
git commit -m "feat(catalog-row): render NEW pill + row tint when row id in new_ids"
```

---

### Task 5: Catalog route — compute `new_ids` and pass it to the template

**Files:**
- Modify: `regwatch/web/routes/catalog.py:107-178`
- Test: `tests/integration/test_new_since_last_visit_highlights.py` (NEW)

- [ ] **Step 1: Create the new integration test file with the catalog test**

Create `tests/integration/test_new_since_last_visit_highlights.py`:

```python
"""Highlight 'new since last visit' rows on Catalog/ICT/Drafts/Deadlines."""
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from regwatch.db.models import LifecycleStage, Regulation, RegulationType, Setting
from tests.integration.test_app_smoke import _client


def _seed_regulation(client, *, ref, lifecycle, is_ict, created_at, deadline=None):
    sf = client.app.state.session_factory
    with sf() as session:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number=ref,
            title=ref,
            issuing_authority="CSSF",
            lifecycle_stage=lifecycle,
            is_ict=is_ict,
            source_of_truth="SEED",
            url=f"https://example.com/{ref}",
            transposition_deadline=deadline,
            created_at=created_at,
        )
        session.add(reg)
        session.commit()
        return reg.regulation_id


def _set_last_visit(client, *, key, ts):
    sf = client.app.state.session_factory
    with sf() as session:
        existing = session.get(Setting, key)
        if existing is None:
            session.add(Setting(key=key, value=ts.isoformat(), updated_at=ts))
        else:
            existing.value = ts.isoformat()
            existing.updated_at = ts
        session.commit()


def _row_block(html: str, reference: str) -> str:
    """Return the <tr>...</tr> block that contains the given reference number."""
    # rows are emitted as <tr ...>...{{ ref }}...</tr>; split is robust enough
    # for these template-driven tests.
    parts = html.split("<tr")
    for part in parts[1:]:
        block = "<tr" + part.split("</tr>", 1)[0] + "</tr>"
        if reference in block:
            return block
    raise AssertionError(f"reference {reference!r} not found in any <tr> block")


def test_catalog_highlights_new_rows_on_first_visit_after_cutoff(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client, ref="OLDREG", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=cutoff - timedelta(days=1),
    )
    _seed_regulation(
        client, ref="NEWREG", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=datetime.now(UTC),
    )

    resp = client.get("/catalog")
    assert resp.status_code == 200

    new_block = _row_block(resp.text, "NEWREG")
    old_block = _row_block(resp.text, "OLDREG")

    assert "bg-amber-50" in new_block
    assert ">NEW<" in new_block
    assert "bg-amber-50" not in old_block
    assert ">NEW<" not in old_block


def test_catalog_no_highlight_on_second_visit(tmp_path: Path, monkeypatch) -> None:
    """After the first visit advances the cutoff, the row is no longer 'new'."""
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client, ref="NEWREG", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=datetime.now(UTC),
    )

    # First visit: row highlighted, cutoff advances to now.
    client.get("/catalog")
    # Second visit: cutoff is now in the future relative to NEWREG.created_at.
    resp = client.get("/catalog")
    assert resp.status_code == 200
    block = _row_block(resp.text, "NEWREG")
    assert ">NEW<" not in block


def test_catalog_no_highlight_when_no_prior_visit(
    tmp_path: Path, monkeypatch
) -> None:
    """First-ever visit (no last_visit_catalog) should not highlight anything,
    matching the badge which is also 0 in this case."""
    client = _client(tmp_path, monkeypatch)
    _seed_regulation(
        client, ref="ANYREG", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=datetime.now(UTC),
    )

    resp = client.get("/catalog")
    assert resp.status_code == 200
    block = _row_block(resp.text, "ANYREG")
    assert ">NEW<" not in block
```

- [ ] **Step 2: Run the new tests, verify they fail**

```powershell
pytest tests/integration/test_new_since_last_visit_highlights.py -v
```

Expected: `test_catalog_highlights_new_rows_on_first_visit_after_cutoff` FAILS at `assert ">NEW<" in new_block` (route doesn't pass `new_ids` yet). The other two should already PASS (nothing renders `NEW`).

- [ ] **Step 3: Wire `new_ids` into the catalog route**

Edit `regwatch/web/routes/catalog.py`. Inside the `with request.app.state.session_factory() as session:` block (around line 107-151), replace the existing `SidebarBadgeService(session).mark_visited("catalog")` line and the lines around the render with the version below.

Specifically, change this block (catalog.py:150-151):

```python
        SidebarBadgeService(session).mark_visited("catalog")
        session.commit()
```

to:

```python
        previous_cutoff = SidebarBadgeService(session).mark_visited("catalog")
        session.commit()

    new_ids: set[int] = (
        {r.regulation_id for r in regs if r.created_at > previous_cutoff}
        if previous_cutoff is not None else set()
    )
```

Then add `"new_ids": new_ids` to the dict passed to `render_page`. The full updated `render_page` call is:

```python
    response = render_page(
        request,
        "catalog/list.html",
        {
            "active": "catalog",
            "regulations": regs,
            "flt": flt,
            "status_by_reg": status_by_reg,
            "effective_lifecycle": effective_lifecycle,
            "effective_ict": effective_ict,
            "show_amendments": show_amendments,
            "amendment_counts": amendment_counts,
            "flash_message": flash_message,
            "new_ids": new_ids,
        },
    )
```

Note: the `new_ids` computation happens **outside** the `with` block because `regs` is a list of plain DTOs and doesn't need the session. The session has already been committed.

- [ ] **Step 4: Run the catalog tests, verify they pass**

```powershell
pytest tests/integration/test_new_since_last_visit_highlights.py -v -k "catalog"
pytest tests/integration/test_catalog_view.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add regwatch/web/routes/catalog.py tests/integration/test_new_since_last_visit_highlights.py
git commit -m "feat(catalog): highlight rows added since last visit"
```

---

### Task 6: Drafts route — same `new_ids` wiring

**Files:**
- Modify: `regwatch/web/routes/drafts.py`
- Test: append to `tests/integration/test_new_since_last_visit_highlights.py`

The Drafts page uses the same `partials/catalog_row.html`, so Task 4's template change already supports it. Only the route needs the wiring.

- [ ] **Step 1: Add the drafts test**

Append to `tests/integration/test_new_since_last_visit_highlights.py`:

```python
def test_drafts_highlights_new_drafty_rows(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_drafts", ts=cutoff)
    _seed_regulation(
        client, ref="OLDDRAFT", lifecycle=LifecycleStage.PROPOSAL, is_ict=False,
        created_at=cutoff - timedelta(days=1),
    )
    _seed_regulation(
        client, ref="NEWDRAFT", lifecycle=LifecycleStage.CONSULTATION, is_ict=False,
        created_at=datetime.now(UTC),
    )

    resp = client.get("/drafts")
    assert resp.status_code == 200

    new_block = _row_block(resp.text, "NEWDRAFT")
    old_block = _row_block(resp.text, "OLDDRAFT")

    assert "bg-amber-50" in new_block
    assert ">NEW<" in new_block
    assert "bg-amber-50" not in old_block
    assert ">NEW<" not in old_block
```

- [ ] **Step 2: Run, verify the new drafts test fails**

```powershell
pytest tests/integration/test_new_since_last_visit_highlights.py::test_drafts_highlights_new_drafty_rows -v
```

Expected: FAIL at `assert ">NEW<" in new_block`.

- [ ] **Step 3: Wire `new_ids` into the drafts route**

Replace the body of `drafts` in `regwatch/web/routes/drafts.py` with:

```python
@router.get("/drafts", response_class=HTMLResponse)
def drafts(request: Request) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(
            RegulationFilter(
                authorization_type=active_entity_type(request),
                lifecycle_stages=[
                    "CONSULTATION",
                    "PROPOSAL",
                    "DRAFT_BILL",
                    "ADOPTED_NOT_IN_FORCE",
                ],
            )
        )
        previous_cutoff = SidebarBadgeService(session).mark_visited("drafts")
        session.commit()

    new_ids: set[int] = (
        {r.regulation_id for r in regs if r.created_at > previous_cutoff}
        if previous_cutoff is not None else set()
    )

    return render_page(
        request,
        "drafts/list.html",
        {
            "active": "drafts",
            "regulations": regs,
            "new_ids": new_ids,
        },
    )
```

- [ ] **Step 4: Run the drafts tests**

```powershell
pytest tests/integration/test_new_since_last_visit_highlights.py::test_drafts_highlights_new_drafty_rows -v
pytest tests/integration/test_drafts_deadlines_ict_views.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add regwatch/web/routes/drafts.py tests/integration/test_new_since_last_visit_highlights.py
git commit -m "feat(drafts): highlight rows added since last visit"
```

---

### Task 7: ICT — inline template change + route wiring

**Files:**
- Modify: `regwatch/web/templates/ict/list.html`
- Modify: `regwatch/web/routes/ict.py`
- Test: append to `tests/integration/test_new_since_last_visit_highlights.py`

- [ ] **Step 1: Add the ICT test**

Append to `tests/integration/test_new_since_last_visit_highlights.py`:

```python
def test_ict_highlights_new_in_force_ict_rows(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_ict", ts=cutoff)
    _seed_regulation(
        client, ref="OLDICT", lifecycle=LifecycleStage.IN_FORCE, is_ict=True,
        created_at=cutoff - timedelta(days=1),
    )
    _seed_regulation(
        client, ref="NEWICT", lifecycle=LifecycleStage.IN_FORCE, is_ict=True,
        created_at=datetime.now(UTC),
    )

    resp = client.get("/ict")
    assert resp.status_code == 200

    new_block = _row_block(resp.text, "NEWICT")
    old_block = _row_block(resp.text, "OLDICT")

    assert "bg-amber-50" in new_block
    assert ">NEW<" in new_block
    assert ">NEW<" not in old_block
```

- [ ] **Step 2: Run, verify it fails**

```powershell
pytest tests/integration/test_new_since_last_visit_highlights.py::test_ict_highlights_new_in_force_ict_rows -v
```

Expected: FAIL at `assert ">NEW<" in new_block`.

- [ ] **Step 3: Update `ict/list.html`**

Replace `regwatch/web/templates/ict/list.html` with:

```html
{% extends "base.html" %}
{% block title %}RegWatch — ICT / DORA{% endblock %}
{% block content %}
  <div class="flex justify-between items-center mb-4">
    <h1 class="text-2xl font-bold">ICT / DORA</h1>
    <form method="post" action="/ict/refresh">
      <button type="submit" class="px-3 py-2 bg-slate-800 text-white rounded text-sm hover:bg-slate-700">
        Refresh catalog
      </button>
    </form>
  </div>

  {% include "partials/entity_type_pill.html" %}
  {% set new_ids_set = new_ids|default(none) %}
  <table class="w-full bg-white border rounded shadow-sm text-sm">
    <thead class="bg-slate-100 text-xs uppercase text-slate-600">
      <tr>
        <th class="text-left p-2">Reference</th>
        <th class="text-left p-2">Title</th>
        <th class="text-left p-2">Authority</th>
        <th class="text-left p-2">DORA Pillar</th>
        <th class="text-left p-2">Lifecycle</th>
        <th class="text-left p-2">Status</th>
        <th class="text-left p-2">Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for r in regulations %}
      {% set is_new = new_ids_set is not none and r.regulation_id in new_ids_set %}
      <tr class="border-t {% if r.needs_review or is_new %}bg-amber-50{% endif %}">
        <td class="p-2 font-mono">
          {{ r.reference_number }}
          {% if is_new %}
            <span class="ml-2 px-1.5 py-0.5 bg-amber-100 text-amber-800 text-xs rounded font-semibold"
                  title="Added since your last visit">NEW</span>
          {% endif %}
        </td>
        <td class="p-2">
          <a href="/regulations/{{ r.regulation_id }}" class="text-blue-700 hover:underline">{{ r.title }}</a>
        </td>
        <td class="p-2">{{ r.issuing_authority }}</td>
        <td class="p-2">{{ r.dora_pillar or '—' }}</td>
        <td class="p-2">{{ r.lifecycle_stage }}</td>
        <td class="p-2">
          {% if r.needs_review %}
            <span class="px-2 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800">Needs review</span>
          {% else %}
            <span class="text-green-700 text-xs">Confirmed</span>
          {% endif %}
        </td>
        <td class="p-2">
          <form method="post" action="/ict/{{ r.regulation_id }}/unset-ict" class="inline">
            <button type="submit" class="px-2 py-1 bg-red-100 rounded hover:bg-red-200 text-red-800 text-xs">
              Not ICT
            </button>
          </form>
        </td>
      </tr>
      {% else %}
        <tr><td colspan="7" class="p-4 text-center text-slate-500">No ICT-flagged regulations.</td></tr>
      {% endfor %}
    </tbody>
  </table>
{% endblock %}
```

- [ ] **Step 4: Wire `new_ids` into the ICT route**

Replace the body of the `ict` handler in `regwatch/web/routes/ict.py` with:

```python
@router.get("/ict", response_class=HTMLResponse)
def ict(request: Request) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        regs = RegulationService(session).list(
            RegulationFilter(
                is_ict=True,
                lifecycle_stages=["IN_FORCE"],
                authorization_type=active_entity_type(request),
            )
        )
        previous_cutoff = SidebarBadgeService(session).mark_visited("ict")
        session.commit()

    new_ids: set[int] = (
        {r.regulation_id for r in regs if r.created_at > previous_cutoff}
        if previous_cutoff is not None else set()
    )

    return render_page(
        request,
        "ict/list.html",
        {"active": "ict", "regulations": regs, "new_ids": new_ids},
    )
```

- [ ] **Step 5: Run the ICT tests**

```powershell
pytest tests/integration/test_new_since_last_visit_highlights.py::test_ict_highlights_new_in_force_ict_rows -v
pytest tests/integration/test_drafts_deadlines_ict_views.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```powershell
git add regwatch/web/templates/ict/list.html regwatch/web/routes/ict.py tests/integration/test_new_since_last_visit_highlights.py
git commit -m "feat(ict): highlight rows added since last visit"
```

---

### Task 8: Deadlines — inline template change + route wiring

**Files:**
- Modify: `regwatch/web/templates/deadlines/list.html`
- Modify: `regwatch/web/routes/deadlines.py`
- Test: append to `tests/integration/test_new_since_last_visit_highlights.py`

- [ ] **Step 1: Add the deadlines test**

Append to `tests/integration/test_new_since_last_visit_highlights.py`:

```python
def test_deadlines_highlights_new_regulations_deadlines(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_deadlines", ts=cutoff)

    today = date.today()
    in_window = today + timedelta(days=90)

    _seed_regulation(
        client, ref="OLDDL", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=cutoff - timedelta(days=1), deadline=in_window,
    )
    _seed_regulation(
        client, ref="NEWDL", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        created_at=datetime.now(UTC), deadline=in_window,
    )

    resp = client.get("/deadlines")
    assert resp.status_code == 200

    new_block = _row_block(resp.text, "NEWDL")
    old_block = _row_block(resp.text, "OLDDL")

    assert "bg-amber-50" in new_block
    assert ">NEW<" in new_block
    assert ">NEW<" not in old_block
```

- [ ] **Step 2: Run, verify it fails**

```powershell
pytest tests/integration/test_new_since_last_visit_highlights.py::test_deadlines_highlights_new_regulations_deadlines -v
```

Expected: FAIL at `assert ">NEW<" in new_block`.

- [ ] **Step 3: Update `deadlines/list.html`**

Replace `regwatch/web/templates/deadlines/list.html` with:

```html
{% extends "base.html" %}
{% block title %}RegWatch — Deadlines{% endblock %}
{% block content %}
  <div class="flex justify-between items-center mb-4">
    <h1 class="text-2xl font-bold">Deadlines</h1>
    <label class="flex items-center gap-2 text-sm">
      <input type="checkbox"
             {% if show_completed %}checked{% endif %}
             onchange="window.location.href='/deadlines?show_completed=' + this.checked">
      Show completed
    </label>
  </div>

  {% include "partials/entity_type_pill.html" %}

  {% set new_ids_set = new_ids|default(none) %}
  <table class="w-full bg-white border rounded shadow-sm text-sm">
    <thead class="bg-slate-100 text-xs uppercase text-slate-600">
      <tr>
        <th class="text-left p-2">Reference</th>
        <th class="text-left p-2">Title</th>
        <th class="text-left p-2">Kind</th>
        <th class="text-left p-2">Due</th>
        <th class="text-left p-2">Days</th>
        <th class="text-left p-2">Band</th>
        <th class="text-left p-2">Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for d in deadlines %}
      {% set is_new = new_ids_set is not none and d.regulation_id in new_ids_set %}
      <tr id="deadline-{{ d.regulation_id }}-{{ d.kind }}"
          class="border-t {% if is_new %}bg-amber-50{% endif %} {% if d.done %}opacity-50{% endif %}">
        <td class="p-2 font-mono">
          {{ d.reference_number }}
          {% if is_new %}
            <span class="ml-2 px-1.5 py-0.5 bg-amber-100 text-amber-800 text-xs rounded font-semibold"
                  title="Added since your last visit">NEW</span>
          {% endif %}
        </td>
        <td class="p-2">{{ d.title }}</td>
        <td class="p-2">{{ d.kind }}</td>
        <td class="p-2">{{ d.due_date }}</td>
        <td class="p-2">{{ d.days_until }}</td>
        <td class="p-2">
          <span class="px-2 py-0.5 rounded text-xs font-semibold
            {% if d.severity_band == 'OVERDUE' %}bg-red-200 text-red-900
            {% elif d.severity_band == 'RED' %}bg-red-100 text-red-800
            {% elif d.severity_band == 'AMBER' %}bg-amber-100 text-amber-800
            {% elif d.severity_band == 'BLUE' %}bg-blue-100 text-blue-800
            {% else %}bg-slate-100 text-slate-700{% endif %}">
            {{ d.severity_band }}
          </span>
        </td>
        <td class="p-2">
          {% if not d.done %}
          <button class="px-2 py-1 bg-green-100 rounded hover:bg-green-200 text-green-800 text-xs"
                  hx-post="/deadlines/{{ d.regulation_id }}/dismiss"
                  hx-vals='{"kind": "{{ d.kind }}"}'
                  hx-target="#deadline-{{ d.regulation_id }}-{{ d.kind }}"
                  hx-swap="outerHTML">Done</button>
          <button class="px-2 py-1 bg-slate-100 rounded hover:bg-slate-200 text-slate-600 text-xs"
                  hx-post="/deadlines/{{ d.regulation_id }}/dismiss"
                  hx-vals='{"kind": "{{ d.kind }}"}'
                  hx-target="#deadline-{{ d.regulation_id }}-{{ d.kind }}"
                  hx-swap="outerHTML">N/A</button>
          {% else %}
          <button class="px-2 py-1 bg-slate-100 rounded hover:bg-slate-200 text-slate-600 text-xs"
                  hx-post="/deadlines/{{ d.regulation_id }}/restore"
                  hx-vals='{"kind": "{{ d.kind }}"}'
                  hx-target="#deadline-{{ d.regulation_id }}-{{ d.kind }}"
                  hx-swap="outerHTML">Restore</button>
          {% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="7" class="p-4 text-center text-slate-500">No upcoming deadlines.</td></tr>
      {% endfor %}
    </tbody>
  </table>
{% endblock %}
```

- [ ] **Step 4: Wire `new_ids` into the deadlines route**

Replace the body of the `deadlines` handler in `regwatch/web/routes/deadlines.py` with:

```python
@router.get("/deadlines", response_class=HTMLResponse)
def deadlines(
    request: Request,
    show_completed: bool = False,
) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        items = svc.upcoming(
            window_days=730,
            show_completed=show_completed,
            authorization_type=active_entity_type(request),
        )
        previous_cutoff = SidebarBadgeService(session).mark_visited("deadlines")
        session.commit()

    new_ids: set[int] = (
        {d.regulation_id for d in items if d.regulation_created_at > previous_cutoff}
        if previous_cutoff is not None else set()
    )

    return render_page(
        request,
        "deadlines/list.html",
        {
            "active": "deadlines",
            "deadlines": items,
            "show_completed": show_completed,
            "new_ids": new_ids,
        },
    )
```

- [ ] **Step 5: Run the deadlines tests**

```powershell
pytest tests/integration/test_new_since_last_visit_highlights.py::test_deadlines_highlights_new_regulations_deadlines -v
pytest tests/integration/test_drafts_deadlines_ict_views.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```powershell
git add regwatch/web/templates/deadlines/list.html regwatch/web/routes/deadlines.py tests/integration/test_new_since_last_visit_highlights.py
git commit -m "feat(deadlines): highlight rows added since last visit"
```

---

### Task 9: Full-suite green check

**Files:** none.

- [ ] **Step 1: Run the full test suite**

```powershell
pytest
```

Expected: same baseline pass count as before this branch started (the project notes 15 pre-existing tiktoken/SSL failures on Windows that are documented in memory — those remain unchanged; everything else PASSES).

If the pre-existing 15 failures are inconvenient, the documented workaround is:

```powershell
pytest --ignore=tests/integration/test_indexing.py --ignore=tests/integration/test_indexing_embed_text.py --ignore=tests/integration/test_cli_reindex.py
```

(see memory entry "Test env: tiktoken SSL" for the full ignore list.)

- [ ] **Step 2: Run `ruff` and `mypy`**

```powershell
ruff check regwatch
mypy regwatch
```

Expected: no new findings introduced by this branch.

- [ ] **Step 3: Final commit if anything was tweaked during cleanup**

If everything was clean, no commit is needed — the per-task commits are the final state.
