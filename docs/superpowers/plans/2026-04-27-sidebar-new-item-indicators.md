# Sidebar New-Item Indicators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a numeric amber badge next to each major sidebar entry (Inbox, Catalog, ICT/DORA, Drafts, Deadlines) showing the count of items added since the user last visited that section. Visiting a section's page clears that section's badge.

**Architecture:** Per-section last-visit timestamps live in the existing `setting` key-value table. A new `SidebarBadgeService` runs five `SELECT count(*)` queries filtered by those timestamps. A new `render_page` helper wraps `templates.TemplateResponse` so every full-page route auto-injects `sidebar_badges` into the render context — partials and HTMX fragment routes keep using `templates.TemplateResponse` directly. Adds a `created_at` column to `Regulation` (with a backfill migration) to support catalog/ICT/drafts/deadlines counts.

**Tech Stack:** Python 3.12, SQLAlchemy 2, FastAPI, Jinja2/HTMX/Tailwind, pytest.

**Spec:** `docs/superpowers/specs/2026-04-27-sidebar-new-item-indicators-design.md`

---

## File map

**Create:**
- `regwatch/services/sidebar_badges.py` — `SidebarBadgeService` + `SidebarBadges` DTO
- `regwatch/web/templates_context.py` — `render_page(request, template, context)` helper
- `tests/unit/test_sidebar_badges.py` — service unit tests
- `tests/unit/test_render_page.py` — render_page unit test
- `tests/unit/test_regulation_created_at_migration.py` — migration test
- `tests/integration/test_sidebar_badges_view.py` — end-to-end badge clearing

**Modify:**
- `regwatch/db/models.py` — add `Regulation.created_at`
- `regwatch/db/migrations.py` — add `migrate_regulation_created_at`
- `regwatch/main.py` — call the new migration
- `regwatch/web/templates/partials/sidebar.html` — render the amber pill
- `regwatch/web/routes/inbox.py` — `render_page` + `mark_visited("inbox")`
- `regwatch/web/routes/catalog.py` — `render_page` + `mark_visited("catalog")`
- `regwatch/web/routes/ict.py` — `render_page` + `mark_visited("ict")`
- `regwatch/web/routes/drafts.py` — `render_page` + `mark_visited("drafts")`
- `regwatch/web/routes/deadlines.py` — `render_page` + `mark_visited("deadlines")`
- `regwatch/web/routes/dashboard.py` — `render_page` (no clearing)
- `regwatch/web/routes/regulation_detail.py` — `render_page`
- `regwatch/web/routes/chat.py` — `render_page` for chat_list, chat_new, chat_session
- `regwatch/web/routes/settings.py` — `render_page` for settings_view, setup_view, extraction_fields_page
- `regwatch/web/routes/schedules.py` — `render_page` for schedules_page
- `regwatch/web/routes/discovery.py` — `render_page` for runs_list, run_page
- `regwatch/web/routes/analysis.py` — `render_page` for run_page

---

### Task 1: Add `Regulation.created_at` column with migration

**Files:**
- Modify: `regwatch/db/models.py`
- Modify: `regwatch/db/migrations.py`
- Modify: `regwatch/main.py`
- Create: `tests/unit/test_regulation_created_at_migration.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/unit/test_regulation_created_at_migration.py`:

```python
"""Tests for the regulation.created_at backfill migration."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text


def _engine_with_old_regulation_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE regulation (
                regulation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                type VARCHAR(50) NOT NULL,
                reference_number VARCHAR(100) NOT NULL,
                title TEXT NOT NULL,
                issuing_authority VARCHAR(100) NOT NULL,
                lifecycle_stage VARCHAR(40) NOT NULL,
                is_ict BOOLEAN DEFAULT 0,
                url VARCHAR(500) NOT NULL,
                source_of_truth VARCHAR(20) NOT NULL
            )
            """
        ))
        conn.execute(text(
            """
            INSERT INTO regulation
            (type, reference_number, title, issuing_authority, lifecycle_stage,
             is_ict, url, source_of_truth)
            VALUES
            ('CSSF_CIRCULAR', 'CSSF 18/698', 't1', 'CSSF', 'IN_FORCE',
             0, 'https://example.com/1', 'SEED'),
            ('CSSF_CIRCULAR', 'CSSF 20/750', 't2', 'CSSF', 'IN_FORCE',
             1, 'https://example.com/2', 'SEED')
            """
        ))
    return engine


def test_migration_adds_column_and_backfills(tmp_path):
    from regwatch.db.migrations import migrate_regulation_created_at

    engine = _engine_with_old_regulation_table(tmp_path)
    before = datetime.now(UTC)
    migrate_regulation_created_at(engine)
    after = datetime.now(UTC)

    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(regulation)"))]
        assert "created_at" in cols
        rows = conn.execute(text("SELECT created_at FROM regulation")).all()
        assert len(rows) == 2
        for (ts_str,) in rows:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            assert before <= ts <= after


def test_migration_is_idempotent(tmp_path):
    from regwatch.db.migrations import migrate_regulation_created_at

    engine = _engine_with_old_regulation_table(tmp_path)
    migrate_regulation_created_at(engine)
    migrate_regulation_created_at(engine)  # second call must be a no-op


def test_migration_handles_missing_table(tmp_path):
    from regwatch.db.migrations import migrate_regulation_created_at

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    migrate_regulation_created_at(engine)  # must not raise
```

- [ ] **Step 2: Run the migration test, confirm fail**

Activate venv: `. .venv/Scripts/activate`
Run: `pytest tests/unit/test_regulation_created_at_migration.py -v`
Expected: FAIL with `ImportError: cannot import name 'migrate_regulation_created_at' from 'regwatch.db.migrations'`.

- [ ] **Step 3: Add `created_at` to the `Regulation` model**

In `regwatch/db/models.py`, find the `Regulation` class (line ~121) and add the new column at the end of the column list, just before the `aliases` relationship (around line 148):

```python
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=lambda: datetime.now(UTC), index=True
    )
```

The required imports (`UTC`, `datetime`, `TZDateTime`) are already at the top of the file.

- [ ] **Step 4: Add the migration function**

In `regwatch/db/migrations.py`, append:

```python
def migrate_regulation_created_at(engine: Engine) -> None:
    """Add regulation.created_at and backfill existing rows to the migration time.

    Backfilling to NOW() at migration time means existing regulations
    will not count as 'new' once the user has visited each section once.
    Idempotent: returns cleanly if the column already exists or the
    table doesn't exist yet.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

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

- [ ] **Step 5: Wire the migration into `create_app`**

In `regwatch/main.py`, find the existing migration call (line 52-53):

```python
    from regwatch.db.migrations import migrate_discovery_run_item_columns
    migrate_discovery_run_item_columns(engine)
```

Replace with:

```python
    from regwatch.db.migrations import (
        migrate_discovery_run_item_columns,
        migrate_regulation_created_at,
    )
    migrate_discovery_run_item_columns(engine)
    migrate_regulation_created_at(engine)
```

- [ ] **Step 6: Run migration tests + full unit suite**

Run: `pytest tests/unit/test_regulation_created_at_migration.py -v`
Expected: 3 PASS.

Run: `pytest tests/unit -q`
Expected: all PASS (the existing model tests pick up the new column automatically because they use `Base.metadata.create_all`).

- [ ] **Step 7: Run lint and type-check**

Run: `ruff check regwatch/db/models.py regwatch/db/migrations.py regwatch/main.py && mypy regwatch/db/models.py regwatch/db/migrations.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add regwatch/db/models.py regwatch/db/migrations.py regwatch/main.py tests/unit/test_regulation_created_at_migration.py
git commit -m "feat(db): add Regulation.created_at with backfill migration"
```

---

### Task 2: SidebarBadgeService

**Files:**
- Create: `regwatch/services/sidebar_badges.py`
- Create: `tests/unit/test_sidebar_badges.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sidebar_badges.py`:

```python
"""Unit tests for SidebarBadgeService."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationType,
    Setting,
    UpdateEvent,
)
from regwatch.services.sidebar_badges import (
    SECTION_KEYS,
    SidebarBadgeService,
)


def _session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'badges.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_regulation(session, *, ref, lifecycle, is_ict, deadline=None, created_at=None):
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
        created_at=created_at or datetime.now(UTC),
    )
    session.add(reg)
    session.flush()
    return reg


def _add_event(session, *, fetched_at, content_hash):
    ev = UpdateEvent(
        source="cssf_rss",
        source_url=f"https://example.com/ev/{content_hash}",
        title=content_hash,
        published_at=fetched_at,
        fetched_at=fetched_at,
        raw_payload={},
        content_hash=content_hash,
        is_ict=False,
        severity="INFORMATIONAL",
        review_status="NEW",
    )
    session.add(ev)
    session.flush()
    return ev


def test_section_keys_are_the_five_expected_sections():
    assert SECTION_KEYS == {
        "inbox": "last_visit_inbox",
        "catalog": "last_visit_catalog",
        "ict": "last_visit_ict",
        "drafts": "last_visit_drafts",
        "deadlines": "last_visit_deadlines",
    }


def test_missing_setting_keys_return_zero_counts(tmp_path):
    session = _session(tmp_path)
    _add_regulation(
        session, ref="A", lifecycle=LifecycleStage.IN_FORCE, is_ict=True
    )
    session.commit()

    counts = SidebarBadgeService(session).counts()
    assert counts.inbox == 0
    assert counts.catalog == 0
    assert counts.ict == 0
    assert counts.drafts == 0
    assert counts.deadlines == 0


def test_inbox_counts_events_after_last_visit(tmp_path):
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_inbox", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    _add_event(
        session, fetched_at=cutoff - timedelta(days=1), content_hash="old",
    )
    _add_event(
        session, fetched_at=cutoff + timedelta(days=1), content_hash="new1",
    )
    _add_event(
        session, fetched_at=cutoff + timedelta(days=2), content_hash="new2",
    )
    session.commit()

    assert SidebarBadgeService(session).counts().inbox == 2


def test_catalog_counts_regulations_after_last_visit(tmp_path):
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_catalog", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    _add_regulation(
        session, ref="OLD", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=False, created_at=cutoff - timedelta(days=1),
    )
    _add_regulation(
        session, ref="NEW", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=False, created_at=cutoff + timedelta(days=1),
    )
    session.commit()

    assert SidebarBadgeService(session).counts().catalog == 1


def test_ict_counts_only_is_ict_true(tmp_path):
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_ict", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    _add_regulation(
        session, ref="ICT", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=True, created_at=cutoff + timedelta(days=1),
    )
    _add_regulation(
        session, ref="NOT", lifecycle=LifecycleStage.IN_FORCE,
        is_ict=False, created_at=cutoff + timedelta(days=1),
    )
    session.commit()

    assert SidebarBadgeService(session).counts().ict == 1


def test_drafts_counts_only_drafty_lifecycles(tmp_path):
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_drafts", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    for lc in (
        LifecycleStage.CONSULTATION,
        LifecycleStage.PROPOSAL,
        LifecycleStage.DRAFT_BILL,
        LifecycleStage.ADOPTED_NOT_IN_FORCE,
        LifecycleStage.IN_FORCE,  # excluded
        LifecycleStage.REPEALED,  # excluded
    ):
        _add_regulation(
            session, ref=lc.value, lifecycle=lc, is_ict=False,
            created_at=cutoff + timedelta(days=1),
        )
    session.commit()

    assert SidebarBadgeService(session).counts().drafts == 4


def test_deadlines_counts_regulations_with_any_deadline_set(tmp_path):
    from datetime import date
    session = _session(tmp_path)
    cutoff = datetime(2026, 4, 1, tzinfo=UTC)
    session.add(Setting(
        key="last_visit_deadlines", value=cutoff.isoformat(), updated_at=cutoff,
    ))
    _add_regulation(
        session, ref="HAS_DL", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=date(2027, 1, 1), created_at=cutoff + timedelta(days=1),
    )
    _add_regulation(
        session, ref="NO_DL", lifecycle=LifecycleStage.IN_FORCE, is_ict=False,
        deadline=None, created_at=cutoff + timedelta(days=1),
    )
    session.commit()

    assert SidebarBadgeService(session).counts().deadlines == 1


def test_mark_visited_upserts_setting(tmp_path):
    session = _session(tmp_path)
    svc = SidebarBadgeService(session)

    before = datetime.now(UTC)
    svc.mark_visited("inbox")
    session.commit()
    after = datetime.now(UTC)

    row = session.get(Setting, "last_visit_inbox")
    assert row is not None
    stored = datetime.fromisoformat(row.value)
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    assert before <= stored <= after

    # Second call updates, does not duplicate.
    svc.mark_visited("inbox")
    session.commit()
    rows = session.query(Setting).filter(Setting.key == "last_visit_inbox").all()
    assert len(rows) == 1


def test_mark_visited_rejects_unknown_section(tmp_path):
    import pytest
    session = _session(tmp_path)
    svc = SidebarBadgeService(session)
    with pytest.raises(ValueError, match="unknown section"):
        svc.mark_visited("nope")
```

- [ ] **Step 2: Run tests, confirm fail**

Run: `pytest tests/unit/test_sidebar_badges.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regwatch.services.sidebar_badges'`.

- [ ] **Step 3: Implement the service**

Create `regwatch/services/sidebar_badges.py`:

```python
"""Counts of items added since the user's last visit to each sidebar section."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from regwatch.db.models import LifecycleStage, Regulation, Setting, UpdateEvent

SECTION_KEYS: dict[str, str] = {
    "inbox": "last_visit_inbox",
    "catalog": "last_visit_catalog",
    "ict": "last_visit_ict",
    "drafts": "last_visit_drafts",
    "deadlines": "last_visit_deadlines",
}

_DRAFTY_LIFECYCLES = (
    LifecycleStage.CONSULTATION,
    LifecycleStage.PROPOSAL,
    LifecycleStage.DRAFT_BILL,
    LifecycleStage.ADOPTED_NOT_IN_FORCE,
)


@dataclass(frozen=True)
class SidebarBadges:
    inbox: int
    catalog: int
    ict: int
    drafts: int
    deadlines: int


class SidebarBadgeService:
    """Reads and writes per-section last-visit timestamps."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def counts(self) -> SidebarBadges:
        """Return the new-item counts for each section.

        A missing setting key for a section means "user has just visited":
        the count is 0 and no items qualify as new until the next visit.
        """
        return SidebarBadges(
            inbox=self._count_inbox(),
            catalog=self._count_catalog(),
            ict=self._count_ict(),
            drafts=self._count_drafts(),
            deadlines=self._count_deadlines(),
        )

    def mark_visited(self, section: str) -> None:
        """Upsert last_visit_<section> = now."""
        if section not in SECTION_KEYS:
            raise ValueError(f"unknown section: {section!r}")
        key = SECTION_KEYS[section]
        now = datetime.now(UTC)
        existing = self._session.get(Setting, key)
        if existing is None:
            self._session.add(Setting(key=key, value=now.isoformat(), updated_at=now))
        else:
            existing.value = now.isoformat()
            existing.updated_at = now

    def _last_visit(self, section: str) -> datetime | None:
        row = self._session.get(Setting, SECTION_KEYS[section])
        if row is None:
            return None
        ts = datetime.fromisoformat(row.value)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts

    def _count_inbox(self) -> int:
        cutoff = self._last_visit("inbox")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(UpdateEvent.event_id)).where(
                UpdateEvent.fetched_at > cutoff
            )
        )
        return int(n or 0)

    def _count_catalog(self) -> int:
        cutoff = self._last_visit("catalog")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(Regulation.regulation_id)).where(
                Regulation.created_at > cutoff
            )
        )
        return int(n or 0)

    def _count_ict(self) -> int:
        cutoff = self._last_visit("ict")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(Regulation.regulation_id)).where(
                Regulation.created_at > cutoff,
                Regulation.is_ict.is_(True),
            )
        )
        return int(n or 0)

    def _count_drafts(self) -> int:
        cutoff = self._last_visit("drafts")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(Regulation.regulation_id)).where(
                Regulation.created_at > cutoff,
                Regulation.lifecycle_stage.in_(_DRAFTY_LIFECYCLES),
            )
        )
        return int(n or 0)

    def _count_deadlines(self) -> int:
        cutoff = self._last_visit("deadlines")
        if cutoff is None:
            return 0
        n = self._session.scalar(
            select(func.count(Regulation.regulation_id)).where(
                Regulation.created_at > cutoff,
                (
                    Regulation.transposition_deadline.is_not(None)
                    | Regulation.application_date.is_not(None)
                ),
            )
        )
        return int(n or 0)
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `pytest tests/unit/test_sidebar_badges.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Run lint and type-check**

Run: `ruff check regwatch/services/sidebar_badges.py && mypy regwatch/services/sidebar_badges.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add regwatch/services/sidebar_badges.py tests/unit/test_sidebar_badges.py
git commit -m "feat(services): SidebarBadgeService computes per-section new-item counts"
```

---

### Task 3: `render_page` helper

**Files:**
- Create: `regwatch/web/templates_context.py`
- Create: `tests/unit/test_render_page.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_render_page.py`:

```python
"""Tests for the render_page helper that auto-injects sidebar_badges."""
from unittest.mock import MagicMock

from regwatch.services.sidebar_badges import SidebarBadges


def test_render_page_injects_sidebar_badges_into_context(monkeypatch):
    from regwatch.web import templates_context

    captured: dict = {}

    class FakeTemplates:
        def TemplateResponse(self, request, template_name, context):
            captured["request"] = request
            captured["template_name"] = template_name
            captured["context"] = context
            return "rendered"

    fake_session = MagicMock()
    fake_session_factory = MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=fake_session),
        __exit__=MagicMock(return_value=False),
    ))

    fake_request = MagicMock()
    fake_request.app.state.templates = FakeTemplates()
    fake_request.app.state.session_factory = fake_session_factory

    fake_badges = SidebarBadges(
        inbox=2, catalog=0, ict=1, drafts=0, deadlines=3,
    )

    fake_service = MagicMock()
    fake_service.counts.return_value = fake_badges
    monkeypatch.setattr(
        templates_context, "SidebarBadgeService",
        MagicMock(return_value=fake_service),
    )

    out = templates_context.render_page(
        fake_request, "x.html", {"foo": "bar"}
    )

    assert out == "rendered"
    assert captured["template_name"] == "x.html"
    assert captured["context"]["foo"] == "bar"
    assert captured["context"]["sidebar_badges"] is fake_badges


def test_render_page_does_not_overwrite_caller_supplied_sidebar_badges(monkeypatch):
    """Defensive: if a caller passes sidebar_badges explicitly, do not override."""
    from regwatch.web import templates_context

    captured: dict = {}

    class FakeTemplates:
        def TemplateResponse(self, request, template_name, context):
            captured["context"] = context
            return None

    fake_session = MagicMock()
    fake_request = MagicMock()
    fake_request.app.state.templates = FakeTemplates()
    fake_request.app.state.session_factory = MagicMock(
        return_value=MagicMock(
            __enter__=MagicMock(return_value=fake_session),
            __exit__=MagicMock(return_value=False),
        ),
    )

    monkeypatch.setattr(
        templates_context, "SidebarBadgeService",
        MagicMock(return_value=MagicMock(counts=MagicMock(return_value="default"))),
    )

    explicit = SidebarBadges(inbox=99, catalog=0, ict=0, drafts=0, deadlines=0)
    templates_context.render_page(
        fake_request, "x.html", {"sidebar_badges": explicit}
    )

    assert captured["context"]["sidebar_badges"] is explicit
```

- [ ] **Step 2: Run tests, confirm fail**

Run: `pytest tests/unit/test_render_page.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regwatch.web.templates_context'`.

- [ ] **Step 3: Implement the helper**

Create `regwatch/web/templates_context.py`:

```python
"""Render helper that auto-injects sidebar_badges into full-page renders.

Use `render_page` instead of `templates.TemplateResponse` for any view
that extends `base.html`. Partials and HTMX fragment endpoints should
keep using `templates.TemplateResponse` directly — they do not include
the sidebar and the extra DB hit would be wasted.
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from regwatch.services.sidebar_badges import SidebarBadgeService


def render_page(
    request: Request,
    template_name: str,
    context: dict[str, Any],
) -> HTMLResponse:
    """TemplateResponse with `sidebar_badges` auto-injected.

    If the caller already set `sidebar_badges` in *context*, it is
    preserved (used by tests that want to control the sidebar state).
    """
    templates = request.app.state.templates
    if "sidebar_badges" not in context:
        with request.app.state.session_factory() as session:
            badges = SidebarBadgeService(session).counts()
        context = {**context, "sidebar_badges": badges}
    return templates.TemplateResponse(request, template_name, context)
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `pytest tests/unit/test_render_page.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Run lint and type-check**

Run: `ruff check regwatch/web/templates_context.py && mypy regwatch/web/templates_context.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/templates_context.py tests/unit/test_render_page.py
git commit -m "feat(web): render_page helper auto-injects sidebar_badges"
```

---

### Task 4: Update `sidebar.html` to render the badge pill

**Files:**
- Modify: `regwatch/web/templates/partials/sidebar.html`

This task only changes the template. The five top-level entries (Inbox, Catalog, ICT/DORA, Drafts, Deadlines) get a trailing pill rendered conditionally. The Dashboard, Q&A, and Settings parent links and all sub-entries are unchanged.

- [ ] **Step 1: Replace the file with the new content**

Use Write to replace `regwatch/web/templates/partials/sidebar.html` with EXACTLY:

```html
{% macro badge(count) -%}
  {% if count %}
    <span class="ml-2 inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5
                 bg-amber-500 text-white text-xs font-semibold rounded-full shrink-0">
      {{ count if count < 100 else '99+' }}
    </span>
  {% endif %}
{%- endmacro %}

<aside class="w-56 bg-slate-900 text-slate-100 min-h-screen p-4 flex flex-col">
  <div class="text-xl font-bold mb-6">RegWatch</div>
  <nav class="flex flex-col gap-1 text-sm">
    <a href="/" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'dashboard' %}bg-slate-800{% endif %}">Dashboard</a>
    <a href="/inbox" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'inbox' %}bg-slate-800{% endif %}">
      <span>Inbox</span>{{ badge(sidebar_badges.inbox) }}
    </a>
    <a href="/catalog" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'catalog' %}bg-slate-800{% endif %}">
      <span>Catalog</span>{{ badge(sidebar_badges.catalog) }}
    </a>
    <a href="/catalog?authorization=AIFM" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">AIFM</a>
    <a href="/catalog?authorization=CHAPTER15_MANCO" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">Chapter 15 ManCo</a>
    <a href="/ict" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'ict' %}bg-slate-800{% endif %}">
      <span>ICT / DORA</span>{{ badge(sidebar_badges.ict) }}
    </a>
    <a href="/drafts" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'drafts' %}bg-slate-800{% endif %}">
      <span>Drafts</span>{{ badge(sidebar_badges.drafts) }}
    </a>
    <a href="/deadlines" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'deadlines' %}bg-slate-800{% endif %}">
      <span>Deadlines</span>{{ badge(sidebar_badges.deadlines) }}
    </a>
    <a href="/chat" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'chat' %}bg-slate-800{% endif %}">Q&amp;A</a>
    <a href="/settings" class="mt-auto px-3 py-2 rounded hover:bg-slate-800 {% if active == 'settings' %}bg-slate-800{% endif %}">Settings</a>
    <a href="/settings/extraction" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">Extraction Fields</a>
    <a href="/settings/schedules" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">Schedules</a>
  </nav>
</aside>
```

- [ ] **Step 2: Note about template rendering when sidebar_badges is missing**

Jinja2 with default settings does NOT raise on missing dict keys — `sidebar_badges.inbox` evaluates to an undefined value that the `{% if count %}` macro treats as falsy, so no badge renders. This means routes that have not yet been converted to `render_page` (we convert them in Tasks 5–6) will still render correctly with NO badges. No schema-of-the-air violation.

- [ ] **Step 3: Commit**

The template change does not break any existing test because the existing test routes still use `templates.TemplateResponse` (no `sidebar_badges` injected), and Jinja's default undefined behaviour treats it as falsy.

```bash
git add regwatch/web/templates/partials/sidebar.html
git commit -m "feat(web): sidebar template renders amber badge pill on 5 sections"
```

---

### Task 5: Convert the 5 clearing routes to `render_page` + `mark_visited`

**Files:**
- Modify: `regwatch/web/routes/inbox.py`
- Modify: `regwatch/web/routes/catalog.py`
- Modify: `regwatch/web/routes/ict.py`
- Modify: `regwatch/web/routes/drafts.py`
- Modify: `regwatch/web/routes/deadlines.py`

These five routes both render with the badge AND clear that section's badge.

- [ ] **Step 1: Convert `inbox.py`**

In `regwatch/web/routes/inbox.py`, change the imports near the top:

```python
from regwatch.services.inbox import SOURCE_DISPLAY_NAMES, InboxService
from regwatch.services.sidebar_badges import SidebarBadgeService
from regwatch.web.templates_context import render_page
```

Inside `inbox_list`, replace the existing block:

```python
    templates = request.app.state.templates
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        events = svc.list_new(
            source_display=source,
            entity_type=entity_type,
            authorization_types=auth_types,
            show_all=show_all,
        )
    source_options = sorted(set(SOURCE_DISPLAY_NAMES.values()))
    return templates.TemplateResponse(
        request,
        "inbox/list.html",
        {
            "active": "inbox",
            "events": events,
            "source_options": source_options,
            "current_source": source,
            "current_entity_type": entity_type,
            "show_all": show_all,
        },
    )
```

with:

```python
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        events = InboxService(session).list_new(
            source_display=source,
            entity_type=entity_type,
            authorization_types=auth_types,
            show_all=show_all,
        )
        SidebarBadgeService(session).mark_visited("inbox")
        session.commit()
    source_options = sorted(set(SOURCE_DISPLAY_NAMES.values()))
    return render_page(
        request,
        "inbox/list.html",
        {
            "active": "inbox",
            "events": events,
            "source_options": source_options,
            "current_source": source,
            "current_entity_type": entity_type,
            "show_all": show_all,
        },
    )
```

The change collapses the two-session pattern into one (`mark_visited` runs in the same session as the read). Removing the redundant `templates = request.app.state.templates` line is intentional — it's now unused.

- [ ] **Step 2: Confirm the inbox list page still renders**

Run: `pytest tests/integration/test_inbox_view.py -v`
Expected: all PASS.

- [ ] **Step 3: Convert `ict.py`**

In `regwatch/web/routes/ict.py`, add the imports:

```python
from regwatch.services.sidebar_badges import SidebarBadgeService
from regwatch.web.templates_context import render_page
```

Replace the body of the `ict(request)` GET (lines 17-26 today):

```python
@router.get("/ict", response_class=HTMLResponse)
def ict(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(RegulationFilter(is_ict=True))
    return templates.TemplateResponse(
        request,
        "ict/list.html",
        {"active": "ict", "regulations": regs},
    )
```

with:

```python
@router.get("/ict", response_class=HTMLResponse)
def ict(request: Request) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        regs = RegulationService(session).list(RegulationFilter(is_ict=True))
        SidebarBadgeService(session).mark_visited("ict")
        session.commit()
    return render_page(
        request,
        "ict/list.html",
        {"active": "ict", "regulations": regs},
    )
```

Leave `unset_ict` and `refresh_ict` POST routes unchanged — they return `RedirectResponse`, no template render.

- [ ] **Step 4: Convert `drafts.py`**

Mirror the inbox conversion. Final function reads:

```python
@router.get("/drafts", response_class=HTMLResponse)
def drafts(request: Request) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(
            RegulationFilter(
                lifecycle_stages=[
                    "CONSULTATION",
                    "PROPOSAL",
                    "DRAFT_BILL",
                    "ADOPTED_NOT_IN_FORCE",
                ]
            )
        )
        SidebarBadgeService(session).mark_visited("drafts")
        session.commit()
    return render_page(
        request,
        "drafts/list.html",
        {"active": "drafts", "regulations": regs},
    )
```

Add the imports:

```python
from regwatch.services.sidebar_badges import SidebarBadgeService
from regwatch.web.templates_context import render_page
```

- [ ] **Step 5: Convert `deadlines.py`**

Final shape of the existing `deadlines(request, show_completed)` GET:

```python
@router.get("/deadlines", response_class=HTMLResponse)
def deadlines(
    request: Request,
    show_completed: bool = False,
) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        items = svc.upcoming(window_days=730, show_completed=show_completed)
        SidebarBadgeService(session).mark_visited("deadlines")
        session.commit()
    return render_page(
        request,
        "deadlines/list.html",
        {"active": "deadlines", "deadlines": items, "show_completed": show_completed},
    )
```

Add the imports for `SidebarBadgeService` and `render_page`. Leave the POST `dismiss_deadline` and `restore_deadline` endpoints unchanged — they don't render.

- [ ] **Step 6: Convert `catalog.py::catalog` (the GET at line 50)**

Read `regwatch/web/routes/catalog.py` to see the current shape. The function is longer than the others (it has filter/cookie logic). Find the last `return templates.TemplateResponse(...)` inside `def catalog(...)` and:

1. Add imports near the top:
   ```python
   from regwatch.services.sidebar_badges import SidebarBadgeService
   from regwatch.web.templates_context import render_page
   ```
2. Inside the existing `with request.app.state.session_factory() as session:` block (catalog already opens a session), call `SidebarBadgeService(session).mark_visited("catalog")` next to the existing `session.commit()` (or add a commit if there isn't one).
3. Replace the final `templates.TemplateResponse(request, "catalog/list.html", {...})` with `render_page(request, "catalog/list.html", {...})`.

LEAVE the other functions in `catalog.py` alone — `catalog_analyse` and `catalog_discover_cssf` are POSTs that return `RedirectResponse`, not page renders.

- [ ] **Step 7: Run the integration suite for these five routes**

Run: `pytest tests/integration/test_inbox_view.py tests/integration/test_dashboard_view.py tests/integration/test_catalog_view.py tests/integration -k 'deadline or draft or ict' -v`
Expected: all PASS.

- [ ] **Step 8: Run lint and type-check**

Run: `ruff check regwatch/web/routes && mypy regwatch/web/routes/inbox.py regwatch/web/routes/catalog.py regwatch/web/routes/ict.py regwatch/web/routes/drafts.py regwatch/web/routes/deadlines.py`
Expected: clean (or pre-existing patterns only — flag any NEW errors).

- [ ] **Step 9: Commit**

```bash
git add regwatch/web/routes/inbox.py regwatch/web/routes/catalog.py regwatch/web/routes/ict.py regwatch/web/routes/drafts.py regwatch/web/routes/deadlines.py
git commit -m "feat(web): clearing routes use render_page + mark_visited"
```

---

### Task 6: Convert the remaining page-rendering routes to `render_page`

**Files:**
- Modify: `regwatch/web/routes/dashboard.py`
- Modify: `regwatch/web/routes/regulation_detail.py`
- Modify: `regwatch/web/routes/chat.py`
- Modify: `regwatch/web/routes/settings.py`
- Modify: `regwatch/web/routes/schedules.py`
- Modify: `regwatch/web/routes/discovery.py`
- Modify: `regwatch/web/routes/analysis.py`

These routes show the badges but do NOT clear any. Each is a one-line replacement of `templates.TemplateResponse(request, ...)` with `render_page(request, ...)` plus an import.

For each file: add at the top:

```python
from regwatch.web.templates_context import render_page
```

Then for each page-rendering function inside (listed below), replace the final `templates.TemplateResponse(request, "<template>", {...})` with `render_page(request, "<template>", {...})`. If the function only reads `templates = request.app.state.templates` to use it once, delete that line too.

**dashboard.py:**
- `dashboard` (line 15) → 1 replacement.

**regulation_detail.py:**
- `regulation_detail` (line 49) → 1 replacement.

**chat.py:**
- `chat_list` (line 65) → 1 replacement.
- `chat_new` (line 79) → 1 replacement.
- `chat_session` (line 158) → 1 replacement.

DO NOT touch HTMX endpoints in `chat.py` (POST routes that return inline fragments).

**settings.py:**
- `settings_view` (line 24) → 1 replacement.
- `setup_view` (line 75) → 1 replacement.
- `extraction_fields_page` (line 155) → 1 replacement.

DO NOT touch the POST handlers, the upload-pdf endpoint, or anything that returns RedirectResponse / inline HTML fragments.

**schedules.py:**
- `schedules_page` (line 62) → 1 replacement.

**discovery.py:**
- `runs_list` (line 16) → 1 replacement.
- `run_page` (line 31) → 1 replacement.

DO NOT touch `run_status_fragment` (line 52) — it returns the inline status partial, not a full page.

**analysis.py:**
- `run_page` (line 12) → 1 replacement.

DO NOT touch `run_status_fragment`.

- [ ] **Step 1: Make all the replacements**

Edit each file in turn. After each file, verify it still parses by running `ruff check <file>`.

- [ ] **Step 2: Run the full integration suite**

Run: `pytest tests/integration -v`
Expected: all PASS. Any failure is likely a missing import or a route you converted that returns a partial — restore it.

- [ ] **Step 3: Commit**

```bash
git add regwatch/web/routes/dashboard.py regwatch/web/routes/regulation_detail.py regwatch/web/routes/chat.py regwatch/web/routes/settings.py regwatch/web/routes/schedules.py regwatch/web/routes/discovery.py regwatch/web/routes/analysis.py
git commit -m "feat(web): full-page routes use render_page so sidebar badges render"
```

---

### Task 7: Integration test — badge appears, then clears on visit

**Files:**
- Create: `tests/integration/test_sidebar_badges_view.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_sidebar_badges_view.py`:

```python
"""End-to-end: badge appears on sidebar, clears after the section is visited."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

from regwatch.db.models import LifecycleStage, Regulation, RegulationType, Setting

from tests.integration.test_app_smoke import _client


def _seed_regulation(client, *, ref, is_ict, lifecycle, created_at):
    sf = client.app.state.session_factory
    with sf() as session:
        session.add(Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number=ref,
            title=ref,
            issuing_authority="CSSF",
            lifecycle_stage=lifecycle,
            is_ict=is_ict,
            source_of_truth="SEED",
            url=f"https://example.com/{ref}",
            created_at=created_at,
        ))
        session.commit()


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


def test_catalog_badge_shows_then_clears(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client,
        ref="NEWREG",
        is_ict=False,
        lifecycle=LifecycleStage.IN_FORCE,
        created_at=datetime.now(UTC),
    )

    # Render any page (Dashboard) — sidebar should show "1" near the Catalog link.
    resp1 = client.get("/")
    assert resp1.status_code == 200
    assert ">Catalog</span>" in resp1.text or "Catalog" in resp1.text
    # Find the catalog row and assert it carries an amber pill with "1".
    assert 'href="/catalog"' in resp1.text
    catalog_block = resp1.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" in catalog_block
    assert ">1<" in catalog_block

    # Visit /catalog — clears the badge.
    resp2 = client.get("/catalog")
    assert resp2.status_code == 200

    # Render any page again — sidebar's catalog row should NOT carry a pill.
    resp3 = client.get("/")
    assert resp3.status_code == 200
    catalog_block3 = resp3.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" not in catalog_block3


def test_ict_badge_only_counts_is_ict_true(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_ict", ts=cutoff)
    _seed_regulation(
        client, ref="ICT1", is_ict=True,
        lifecycle=LifecycleStage.IN_FORCE, created_at=datetime.now(UTC),
    )
    _seed_regulation(
        client, ref="NOT1", is_ict=False,
        lifecycle=LifecycleStage.IN_FORCE, created_at=datetime.now(UTC),
    )

    resp = client.get("/")
    assert resp.status_code == 200
    ict_block = resp.text.split('href="/ict"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" in ict_block
    assert ">1<" in ict_block


def test_dashboard_link_never_carries_a_badge(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    # Even with everything fresh, the Dashboard link itself has no pill.
    resp = client.get("/")
    dashboard_block = resp.text.split('href="/"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" not in dashboard_block


def test_visiting_inbox_clears_only_inbox_badge(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    _set_last_visit(client, key="last_visit_inbox", ts=cutoff)
    _set_last_visit(client, key="last_visit_catalog", ts=cutoff)
    _seed_regulation(
        client, ref="REG1", is_ict=False,
        lifecycle=LifecycleStage.IN_FORCE, created_at=datetime.now(UTC),
    )
    # Insert one update_event manually for the inbox count.
    sf = client.app.state.session_factory
    from regwatch.db.models import UpdateEvent
    with sf() as session:
        session.add(UpdateEvent(
            source="cssf_rss",
            source_url="https://example.com/ev",
            title="ev",
            published_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
            raw_payload={},
            content_hash="hh" + "0" * 60,
            is_ict=False,
            severity="INFORMATIONAL",
            review_status="NEW",
        ))
        session.commit()

    # Before visiting inbox: both Inbox and Catalog have a pill.
    before = client.get("/")
    inbox_before = before.text.split('href="/inbox"', 1)[1].split("</a>", 1)[0]
    cat_before = before.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" in inbox_before
    assert "bg-amber-500" in cat_before

    # Visit /inbox.
    client.get("/inbox")

    # After: Inbox cleared, Catalog still pillared.
    after = client.get("/")
    inbox_after = after.text.split('href="/inbox"', 1)[1].split("</a>", 1)[0]
    cat_after = after.text.split('href="/catalog"', 1)[1].split("</a>", 1)[0]
    assert "bg-amber-500" not in inbox_after
    assert "bg-amber-500" in cat_after
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/integration/test_sidebar_badges_view.py -v`
Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_sidebar_badges_view.py
git commit -m "test(sidebar): integration tests for badge appear/clear flow"
```

---

### Task 8: Final verification gate

This task is not committed; it just gates "done."

- [ ] **Step 1: Run the full test suite**

Run: `pytest`
Expected: all PASS. Roughly the previous baseline (~492) + 4 (test_regulation_created_at_migration) + 9 (test_sidebar_badges) + 2 (test_render_page) + 4 (test_sidebar_badges_view) = ~511 tests.

- [ ] **Step 2: Run linter and type-checker**

Run: `ruff check regwatch && mypy regwatch 2>&1 | tail -30`
Expected: ruff clean. mypy may show pre-existing errors (the `HTMLResponse` returning-Any pattern, the `Iterable` warning) but should NOT show any new error pattern caused by this work.

- [ ] **Step 3: Manual UI check**

Start the app:

```bash
uvicorn regwatch.main:app --reload
```

Open `http://localhost:8001` and:

1. Open Settings → manually set `last_visit_catalog` to a past timestamp using a SQL tool, OR run the pipeline / reconciliation to produce one new regulation.
2. Verify the **Catalog** sidebar entry shows an amber pill with the count.
3. Click **Catalog**. Verify the page loads.
4. Click another link (e.g., **Dashboard**). Verify the Catalog pill is now gone in the sidebar.
5. Run the pipeline once. Verify a new pill appears on the **Inbox** entry once the pipeline yields new events. Click **Inbox**, then return to Dashboard, and confirm the pill cleared.
6. Confirm Dashboard, Q&A, and Settings entries never carry a pill.
7. Confirm the AIFM, Chapter 15 ManCo, Extraction Fields, and Schedules sub-entries never carry a pill.

If any of those don't match, file a bug.

- [ ] **Step 4: No commit needed** — stop after the manual check.

---

## Verification checklist (used by Task 8)

- [ ] Adding a new regulation makes the Catalog sidebar pill increment by one (Task 7 covers).
- [ ] Adding an ICT regulation increments both Catalog and ICT/DORA pills (Task 7 covers ICT specifically).
- [ ] Visiting `/catalog` clears the Catalog pill on the next render (Task 7).
- [ ] Visiting `/inbox` clears Inbox but NOT Catalog (Task 7 — `test_visiting_inbox_clears_only_inbox_badge`).
- [ ] Dashboard, Q&A, Settings parent, and sub-entries never render a pill (Task 7 — `test_dashboard_link_never_carries_a_badge`; manual check for the others).
- [ ] Drafts pill counts only the four drafty lifecycles (unit tests cover).
- [ ] Deadlines pill counts only regulations that have `transposition_deadline` or `application_date` set (unit tests cover).
- [ ] Pre-existing regulations on a migrated DB never count as "new" once the user has visited each section once (migration backfills `created_at` to migration-time, so the first visit to each section sets `last_visit_<section>` >= migration-time → existing rows fall outside the strict-greater-than predicate).

## Out of scope

- Real-time push updates (the badge updates only on page reload).
- Per-item read tracking (the Inbox `review_status: NEW/SEEN/ARCHIVED` is independent of the sidebar badge).
- Badges on Dashboard, Q&A, Settings parent, or any sidebar sub-entry.
- Tracking transitions: a regulation that became `is_ict=True` later, or whose `transposition_deadline` was filled in later, does NOT count as "new" on the affected section if its `created_at` predates the last visit. This was an explicit choice during brainstorming (definition A).
- Notifying the user via email/desktop notifications.
