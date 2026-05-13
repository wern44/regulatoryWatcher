# Pluggable Entity Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `AuthorizationType` enum (`AIFM`, `CHAPTER15_MANCO`) with a user-editable `EntityType` table, add a global sidebar switcher, and let users add new entity types (PSF sub-types and beyond) from the Settings UI without code changes or restarts.

**Architecture:** Approach A from the design spec. A new `entity_type` SQLAlchemy model becomes the registry. `Authorization.type` and `RegulationApplicability.authorization_type` keep their string slugs and reference rows in the new table by convention (no DB-level FKs). The enum and its `Literal` alias are deleted; six call sites (CSSF discovery, two LLM prompts, three templates) read from the table instead. A new `/settings/entity-types` CRUD page plus a sidebar "Viewing" switcher round out the UI.

**Tech Stack:** SQLAlchemy 2.0 ORM, SQLite, FastAPI + Jinja2 + HTMX, pytest + pytest-httpx, ruff, mypy strict.

**Spec:** [`docs/superpowers/specs/2026-05-13-pluggable-entity-types-design.md`](../specs/2026-05-13-pluggable-entity-types-design.md)

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `regwatch/db/entity_type_seed.py` | Idempotent seeder for the two default entity types. Parallel to `extraction_field_seed.py`. |
| `regwatch/services/entity_types.py` | `EntityTypeService` (CRUD), `EntityTypeDTO`, validation, `prompt_segment()` helper. |
| `regwatch/web/routes/entity_types.py` | Settings → Entity Types CRUD routes + the global "active entity type" cookie POST. |
| `regwatch/web/templates/settings/entity_types.html` | Settings CRUD page. |
| `regwatch/web/templates/settings/_entity_type_row.html` | HTMX row partial for in-place edits. |
| `tests/unit/test_entity_type_model.py` | Model schema / constraints / defaults. |
| `tests/unit/test_entity_type_seed.py` | Seeder idempotency. |
| `tests/unit/test_entity_type_service.py` | CRUD + slug validation + soft-delete. |
| `tests/unit/test_entity_type_prompt_segment.py` | `prompt_segment()` builds the right string. |
| `tests/integration/test_entity_type_routes.py` | Settings CRUD HTTP routes + HTMX fragments. |
| `tests/integration/test_active_entity_type_cookie.py` | Cookie roundtrip + catalog/inbox filtering. |

### Modified files

| Path | Why |
|---|---|
| `regwatch/db/models.py` | Add `EntityType`. Delete `AuthorizationType` enum. Change `Authorization.type` to `String(20)`. |
| `regwatch/db/migrations.py` | Add `migrate_authorization_type_drop_check()` for SQLite CHECK-constraint removal. |
| `regwatch/main.py` | Wire entity-type seed into startup; replace `AuthorizationType(...)` calls in scheduled jobs with slug strings. |
| `regwatch/config.py` | Delete `AuthorizationType` `Literal`; change `AuthorizationConfig.type` to `str`. |
| `regwatch/services/cssf_discovery.py` | Filter-ID and label-map lookups come from the DB. `entity_types` param becomes `list[str]`. |
| `regwatch/services/discovery.py` | LLM classifier prompt built from `prompt_segment()` at call time. |
| `regwatch/pipeline/match/classify.py` | Read entity-type prompt cache via `app.state.entity_type_prompt`. |
| `regwatch/pipeline/match/combined.py` & `regwatch/pipeline/pipeline_factory.py` | Plumb the prompt string into the matcher. |
| `regwatch/services/regulations.py` | `RegulationFilter.authorization_type` becomes `str \| None`. |
| `regwatch/web/templates_context.py` | Inject `entity_types` + `active_entity_type` into render context. |
| `regwatch/web/templates/partials/sidebar.html` | Global "Viewing" switcher; drop hardcoded `AIFM` / `Chapter 15 ManCo` links; add Entity Types under Settings group. |
| `regwatch/web/templates/catalog/list.html` | Data-driven `<option>` list. |
| `regwatch/web/templates/inbox/list.html` | Data-driven `<option>` list. |
| `regwatch/web/routes/catalog.py` | Drop the `Literal` cast; fallback to cookie; write cookie on filter change. |
| `regwatch/web/routes/inbox.py` | Fallback to cookie; write cookie on filter change. |
| `regwatch/cli.py` | `--entity` flag accepts any slug, validates against DB. |
| `config.example.yaml` | Remove `cssf_discovery.entity_filter_ids` block. |
| `tests/conftest.py` | Add `seeded_entity_types` fixture. |
| 5× `tests/integration/test_cssf_discovery_*.py` + `test_cli_discover_cssf.py` + `test_app_smoke.py` + `test_db_models.py` | Seed the new table before exercising CSSF code; drop enum imports. |

---

## Task 1: Add the `EntityType` model

**Files:**
- Modify: `regwatch/db/models.py`
- Test: `tests/unit/test_entity_type_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_entity_type_model.py`:

```python
"""Schema-level tests for the EntityType model."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from regwatch.db.models import Base, EntityType


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        yield s


def test_minimal_row_uses_defaults(session):
    et = EntityType(slug="AIFM", label="AIFM")
    session.add(et)
    session.commit()
    session.refresh(et)
    assert et.entity_type_id is not None
    assert et.active is True
    assert et.sort_order == 100
    assert et.cssf_entity_filter_id is None
    assert et.cssf_detail_labels is None


def test_slug_is_unique(session):
    session.add(EntityType(slug="AIFM", label="AIFM"))
    session.commit()
    session.add(EntityType(slug="AIFM", label="duplicate"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_cssf_detail_labels_roundtrip_as_json(session):
    labels = ["Alternative investment fund manager", "AIFM"]
    et = EntityType(
        slug="AIFM",
        label="AIFM",
        cssf_entity_filter_id=502,
        cssf_detail_labels=labels,
    )
    session.add(et)
    session.commit()
    session.expire_all()
    refetched = session.query(EntityType).filter_by(slug="AIFM").one()
    assert refetched.cssf_detail_labels == labels
    assert refetched.cssf_entity_filter_id == 502
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_entity_type_model.py -v`
Expected: `ImportError: cannot import name 'EntityType' from 'regwatch.db.models'`

- [ ] **Step 3: Add the model**

In `regwatch/db/models.py`, after the existing `class TZDateTime(...)` block and before `class Base`, this is already correct. Add the new class after `class DoraPillar` (around line 88), before `class Entity`:

```python
class EntityType(Base):
    __tablename__ = "entity_type"

    entity_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120))
    cssf_entity_filter_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cssf_detail_labels: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
```

`JSON`, `Boolean`, `Integer`, `String` are already imported at the top of the file. `datetime` and `UTC` are imported.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_entity_type_model.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Verify the full suite still passes (no regression)**

Run: `pytest -x --ignore=tests/live`
Expected: all green (~467 tests, plus 3 new = ~470).

- [ ] **Step 6: Commit**

```bash
git add regwatch/db/models.py tests/unit/test_entity_type_model.py
git commit -m "feat(db): add EntityType model

Schema-level scaffolding for the pluggable entity-type registry. No
consumers yet; AuthorizationType enum still in place."
```

---

## Task 2: Idempotent entity-type seeder

**Files:**
- Create: `regwatch/db/entity_type_seed.py`
- Create: `tests/unit/test_entity_type_seed.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_entity_type_seed.py`:

```python
"""The entity-type seeder is idempotent and matches the legacy mapping."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from regwatch.db.entity_type_seed import seed_default_entity_types
from regwatch.db.models import Base, EntityType


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        yield s


def test_seeds_two_rows_on_empty_table(session):
    inserted = seed_default_entity_types(session)
    session.commit()
    assert inserted == 2
    slugs = sorted(session.scalars(select(EntityType.slug)).all())
    assert slugs == ["AIFM", "CHAPTER15_MANCO"]


def test_aifm_carries_legacy_filter_id_and_labels(session):
    seed_default_entity_types(session)
    session.commit()
    aifm = session.scalar(select(EntityType).where(EntityType.slug == "AIFM"))
    assert aifm.cssf_entity_filter_id == 502
    assert "Alternative investment fund manager" in aifm.cssf_detail_labels
    assert "AIFM" in aifm.cssf_detail_labels


def test_chapter15_carries_legacy_filter_id_and_labels(session):
    seed_default_entity_types(session)
    session.commit()
    row = session.scalar(
        select(EntityType).where(EntityType.slug == "CHAPTER15_MANCO")
    )
    assert row.cssf_entity_filter_id == 2001
    # All five legacy substring patterns survive the migration.
    for pattern in [
        "UCITS management company",
        "UCITS management companies",
        "Chapter 15 management company",
        "Chapter 15 management companies",
        "Management company",
    ]:
        assert pattern in row.cssf_detail_labels


def test_seed_is_idempotent(session):
    first = seed_default_entity_types(session)
    session.commit()
    second = seed_default_entity_types(session)
    session.commit()
    assert first == 2
    assert second == 0
    assert session.scalar(select(EntityType).where(EntityType.slug == "AIFM").exists().select()) is True


def test_seed_skips_when_any_row_already_exists(session):
    session.add(EntityType(slug="PSF_SPECIALISED", label="PSF — Specialised"))
    session.commit()
    inserted = seed_default_entity_types(session)
    session.commit()
    assert inserted == 0  # table not empty — seeder is a no-op
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_entity_type_seed.py -v`
Expected: `ModuleNotFoundError: No module named 'regwatch.db.entity_type_seed'`

- [ ] **Step 3: Write the seeder**

Create `regwatch/db/entity_type_seed.py`:

```python
"""Idempotent seeder for the default entity types.

Inserts AIFM and CHAPTER15_MANCO with the legacy CSSF filter IDs and
detail-page label substrings preserved from
``regwatch.services.cssf_discovery.CSSF_ENTITY_LABEL_TO_AUTH`` and
``CssfDiscoveryConfig.entity_filter_ids``.

Runs at app startup. If the table already has any rows, it's a no-op —
the user is in charge from that point onward (via Settings → Entity Types).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from regwatch.db.models import EntityType

_DEFAULTS: list[dict[str, object]] = [
    {
        "slug": "AIFM",
        "label": "AIFM",
        "cssf_entity_filter_id": 502,
        "cssf_detail_labels": [
            "Alternative investment fund manager",
            "AIFM",
        ],
        "sort_order": 10,
    },
    {
        "slug": "CHAPTER15_MANCO",
        "label": "Chapter 15 ManCo",
        "cssf_entity_filter_id": 2001,
        "cssf_detail_labels": [
            "UCITS management company",
            "UCITS management companies",
            "Chapter 15 management company",
            "Chapter 15 management companies",
            "Management company",
        ],
        "sort_order": 20,
    },
]


def seed_default_entity_types(session: Session) -> int:
    """Insert the two legacy entity types if the table is empty.

    Returns the number of rows inserted.
    """
    has_any = session.scalar(select(EntityType.entity_type_id).limit(1)) is not None
    if has_any:
        return 0
    for spec in _DEFAULTS:
        session.add(EntityType(**spec))  # type: ignore[arg-type]
    session.flush()
    return len(_DEFAULTS)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_entity_type_seed.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add regwatch/db/entity_type_seed.py tests/unit/test_entity_type_seed.py
git commit -m "feat(db): idempotent entity-type seeder for AIFM and CHAPTER15_MANCO

Preserves the legacy filter IDs (502, 2001) and the substring patterns
from CSSF_ENTITY_LABEL_TO_AUTH so existing CSSF detail-page parsing
keeps working byte-for-byte once the consumers are migrated."
```

---

## Task 3: Wire the seeder into `create_app()`

**Files:**
- Modify: `regwatch/main.py:75-78` (after `seed_core_fields`)
- Test: indirectly via the full suite + a new smoke check

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_app_smoke.py`:

```python
def test_app_startup_seeds_entity_types(tmp_path, monkeypatch):
    """create_app() populates the entity_type table on first boot."""
    from sqlalchemy import select

    from regwatch.db.models import EntityType
    client_ctx = _client(tmp_path, monkeypatch)
    with client_ctx as client:
        with client.app.state.session_factory() as s:
            slugs = sorted(s.scalars(select(EntityType.slug)).all())
        assert "AIFM" in slugs
        assert "CHAPTER15_MANCO" in slugs
```

(`_client` is the existing helper at the top of `test_app_smoke.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_app_smoke.py::test_app_startup_seeds_entity_types -v`
Expected: FAIL — `assert "AIFM" in []`.

- [ ] **Step 3: Wire the seeder**

In `regwatch/main.py`, change the existing seed block (lines 75-78) from:

```python
    from regwatch.db.extraction_field_seed import seed_core_fields
    with session_factory() as session:
        seed_core_fields(session)
        session.commit()
```

to:

```python
    from regwatch.db.entity_type_seed import seed_default_entity_types
    from regwatch.db.extraction_field_seed import seed_core_fields
    with session_factory() as session:
        seed_default_entity_types(session)
        seed_core_fields(session)
        session.commit()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_app_smoke.py -v`
Expected: all green, including the new test.

- [ ] **Step 5: Commit**

```bash
git add regwatch/main.py tests/integration/test_app_smoke.py
git commit -m "feat(app): seed default entity types on startup

Runs before seed_core_fields so any later consumer can rely on at
least AIFM and CHAPTER15_MANCO being present."
```

---

## Task 4: `EntityTypeService` (CRUD + DTOs)

**Files:**
- Create: `regwatch/services/entity_types.py`
- Create: `tests/unit/test_entity_type_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_entity_type_service.py`:

```python
"""CRUD service for entity types."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from regwatch.db.entity_type_seed import seed_default_entity_types
from regwatch.db.models import Base, EntityType, RegulationApplicability, Regulation, LifecycleStage, RegulationType
from regwatch.services.entity_types import (
    EntityTypeService,
    InvalidSlugError,
    SlugConflictError,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()
        yield s


def test_list_active_orders_by_sort_then_slug(session):
    session.add(EntityType(slug="PSF_SUPPORT", label="PSF Support", sort_order=30))
    session.add(EntityType(slug="PSF_SPECIALISED", label="PSF Specialised", sort_order=30))
    session.commit()
    svc = EntityTypeService(session)
    rows = svc.list_active()
    slugs = [r.slug for r in rows]
    assert slugs == ["AIFM", "CHAPTER15_MANCO", "PSF_SPECIALISED", "PSF_SUPPORT"]


def test_list_active_hides_inactive_rows(session):
    svc = EntityTypeService(session)
    svc.deactivate(svc.get_by_slug("AIFM").entity_type_id)
    session.commit()
    slugs = [r.slug for r in svc.list_active()]
    assert "AIFM" not in slugs
    assert "CHAPTER15_MANCO" in slugs


def test_create_validates_slug_format(session):
    svc = EntityTypeService(session)
    with pytest.raises(InvalidSlugError):
        svc.create(slug="bad slug", label="x")
    with pytest.raises(InvalidSlugError):
        svc.create(slug="lower_case", label="x")
    with pytest.raises(InvalidSlugError):
        svc.create(slug="A", label="x")  # too short


def test_create_rejects_duplicate_slug(session):
    svc = EntityTypeService(session)
    with pytest.raises(SlugConflictError):
        svc.create(slug="AIFM", label="duplicate")


def test_create_accepts_valid_slug(session):
    svc = EntityTypeService(session)
    dto = svc.create(
        slug="PSF_SPECIALISED",
        label="PSF — Specialised",
        cssf_entity_filter_id=1234,
        cssf_detail_labels=["Specialised PSF"],
        sort_order=30,
    )
    session.commit()
    assert dto.slug == "PSF_SPECIALISED"
    assert dto.active is True
    refetched = session.query(EntityType).filter_by(slug="PSF_SPECIALISED").one()
    assert refetched.cssf_entity_filter_id == 1234


def test_deactivating_keeps_existing_applicability_rows(session):
    """Soft-delete is the key invariant: dropping a type doesn't orphan data."""
    reg = Regulation(
        reference_number="X1",
        type=RegulationType.CSSF_CIRCULAR,
        title="X",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        url="http://x",
        source_of_truth="SEED",
    )
    session.add(reg)
    session.flush()
    session.add(RegulationApplicability(
        regulation_id=reg.regulation_id,
        authorization_type="AIFM",
    ))
    session.commit()

    svc = EntityTypeService(session)
    svc.deactivate(svc.get_by_slug("AIFM").entity_type_id)
    session.commit()

    # The applicability row is untouched.
    rows = session.query(RegulationApplicability).all()
    assert len(rows) == 1
    assert rows[0].authorization_type == "AIFM"


def test_update_changes_filter_id_and_labels(session):
    svc = EntityTypeService(session)
    aifm = svc.get_by_slug("AIFM")
    svc.update(
        aifm.entity_type_id,
        label="AIFM (updated)",
        cssf_entity_filter_id=999,
        cssf_detail_labels=["NewLabel"],
        sort_order=5,
    )
    session.commit()
    refreshed = svc.get_by_slug("AIFM")
    assert refreshed.label == "AIFM (updated)"
    assert refreshed.cssf_entity_filter_id == 999
    assert refreshed.cssf_detail_labels == ["NewLabel"]
    assert refreshed.sort_order == 5


def test_reactivate(session):
    svc = EntityTypeService(session)
    aifm_id = svc.get_by_slug("AIFM").entity_type_id
    svc.deactivate(aifm_id)
    session.commit()
    assert "AIFM" not in [r.slug for r in svc.list_active()]
    svc.reactivate(aifm_id)
    session.commit()
    assert "AIFM" in [r.slug for r in svc.list_active()]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_entity_type_service.py -v`
Expected: `ModuleNotFoundError: No module named 'regwatch.services.entity_types'`

- [ ] **Step 3: Implement the service**

Create `regwatch/services/entity_types.py`:

```python
"""CRUD service for the entity_type registry."""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from regwatch.db.models import EntityType

_SLUG_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,38}[A-Z0-9]$")


class InvalidSlugError(ValueError):
    """Raised when a slug fails the regex check."""


class SlugConflictError(ValueError):
    """Raised when creating a slug that already exists (active or inactive)."""


@dataclass
class EntityTypeDTO:
    entity_type_id: int
    slug: str
    label: str
    cssf_entity_filter_id: int | None
    cssf_detail_labels: list[str] | None
    sort_order: int
    active: bool


def _to_dto(row: EntityType) -> EntityTypeDTO:
    return EntityTypeDTO(
        entity_type_id=row.entity_type_id,
        slug=row.slug,
        label=row.label,
        cssf_entity_filter_id=row.cssf_entity_filter_id,
        cssf_detail_labels=list(row.cssf_detail_labels) if row.cssf_detail_labels else None,
        sort_order=row.sort_order,
        active=row.active,
    )


class EntityTypeService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active(self) -> list[EntityTypeDTO]:
        rows = self._session.scalars(
            select(EntityType)
            .where(EntityType.active.is_(True))
            .order_by(EntityType.sort_order, EntityType.slug)
        ).all()
        return [_to_dto(r) for r in rows]

    def list_all(self) -> list[EntityTypeDTO]:
        rows = self._session.scalars(
            select(EntityType).order_by(EntityType.sort_order, EntityType.slug)
        ).all()
        return [_to_dto(r) for r in rows]

    def get_by_slug(self, slug: str) -> EntityTypeDTO | None:
        row = self._session.scalar(select(EntityType).where(EntityType.slug == slug))
        return _to_dto(row) if row else None

    def get(self, entity_type_id: int) -> EntityTypeDTO | None:
        row = self._session.get(EntityType, entity_type_id)
        return _to_dto(row) if row else None

    def create(
        self,
        *,
        slug: str,
        label: str,
        cssf_entity_filter_id: int | None = None,
        cssf_detail_labels: list[str] | None = None,
        sort_order: int = 100,
    ) -> EntityTypeDTO:
        if not _SLUG_RE.match(slug):
            raise InvalidSlugError(
                f"slug {slug!r} must match {_SLUG_RE.pattern} "
                "(3-40 chars, uppercase A-Z/0-9/_, starts with a letter, ends with letter or digit)"
            )
        if self._session.scalar(select(EntityType).where(EntityType.slug == slug)):
            raise SlugConflictError(f"slug {slug!r} already exists")
        row = EntityType(
            slug=slug,
            label=label,
            cssf_entity_filter_id=cssf_entity_filter_id,
            cssf_detail_labels=cssf_detail_labels or None,
            sort_order=sort_order,
        )
        self._session.add(row)
        self._session.flush()
        return _to_dto(row)

    def update(
        self,
        entity_type_id: int,
        *,
        label: str | None = None,
        cssf_entity_filter_id: int | None | _Unset = _UNSET,
        cssf_detail_labels: list[str] | None | _Unset = _UNSET,
        sort_order: int | None = None,
    ) -> EntityTypeDTO | None:
        row = self._session.get(EntityType, entity_type_id)
        if row is None:
            return None
        if label is not None:
            row.label = label
        if cssf_entity_filter_id is not _UNSET:
            row.cssf_entity_filter_id = cssf_entity_filter_id  # type: ignore[assignment]
        if cssf_detail_labels is not _UNSET:
            row.cssf_detail_labels = cssf_detail_labels  # type: ignore[assignment]
        if sort_order is not None:
            row.sort_order = sort_order
        self._session.flush()
        return _to_dto(row)

    def deactivate(self, entity_type_id: int) -> None:
        row = self._session.get(EntityType, entity_type_id)
        if row is not None:
            row.active = False
            self._session.flush()

    def reactivate(self, entity_type_id: int) -> None:
        row = self._session.get(EntityType, entity_type_id)
        if row is not None:
            row.active = True
            self._session.flush()


class _Unset:
    """Sentinel for update() so that None can mean 'clear the field'."""


_UNSET = _Unset()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_entity_type_service.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/services/entity_types.py tests/unit/test_entity_type_service.py
git commit -m "feat(services): EntityTypeService with CRUD + slug validation

Soft-delete only: deactivate() never removes the row, so existing
RegulationApplicability strings that reference the slug remain
resurrectable via reactivate()."
```

---

## Task 5: `prompt_segment()` helper

**Files:**
- Modify: `regwatch/services/entity_types.py` (add function)
- Create: `tests/unit/test_entity_type_prompt_segment.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_entity_type_prompt_segment.py`:

```python
"""prompt_segment() exposes the active entity-type registry to LLM prompts."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from regwatch.db.entity_type_seed import seed_default_entity_types
from regwatch.db.models import Base, EntityType
from regwatch.services.entity_types import EntityTypeService, prompt_segment


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()
        yield s


def test_prompt_segment_lists_active_rows(session):
    out = prompt_segment(session)
    assert '"AIFM" (AIFM)' in out
    assert '"CHAPTER15_MANCO" (Chapter 15 ManCo)' in out
    assert '"ALL"' in out


def test_prompt_segment_excludes_inactive(session):
    svc = EntityTypeService(session)
    svc.deactivate(svc.get_by_slug("AIFM").entity_type_id)
    session.commit()
    out = prompt_segment(session)
    assert "AIFM" not in out.replace("CHAPTER15", "")  # only the AIFM slug, not the chapter15 substring
    assert "CHAPTER15_MANCO" in out


def test_prompt_segment_orders_by_sort_then_slug(session):
    session.add(EntityType(slug="PSF_SUPPORT", label="PSF Support", sort_order=30))
    session.add(EntityType(slug="PSF_SPECIALISED", label="PSF Specialised", sort_order=30))
    session.commit()
    out = prompt_segment(session)
    lines = [l for l in out.splitlines() if l.startswith('- "')]
    slugs = [l.split('"')[1] for l in lines]
    assert slugs.index("AIFM") < slugs.index("CHAPTER15_MANCO")
    assert slugs.index("CHAPTER15_MANCO") < slugs.index("PSF_SPECIALISED")
    assert slugs.index("PSF_SPECIALISED") < slugs.index("PSF_SUPPORT")
    assert slugs[-1] == "ALL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_entity_type_prompt_segment.py -v`
Expected: `ImportError: cannot import name 'prompt_segment'`

- [ ] **Step 3: Add the function**

Append to `regwatch/services/entity_types.py`:

```python
def prompt_segment(session: Session) -> str:
    """Return a bullet list of active entity-type slugs for inclusion in LLM prompts.

    The returned string ends with an ``"ALL"`` sentinel meaning "applies to
    all financial entities". Used by both the CSSF classifier
    (``services/discovery.py``) and the per-document classifier
    (``pipeline/match/classify.py``).
    """
    rows = session.scalars(
        select(EntityType)
        .where(EntityType.active.is_(True))
        .order_by(EntityType.sort_order, EntityType.slug)
    ).all()
    bullets = "\n".join(f'- "{r.slug}" ({r.label})' for r in rows)
    return (
        "Valid entity_type slugs:\n"
        f"{bullets}\n"
        '- "ALL" (applies broadly to all financial entities)'
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_entity_type_prompt_segment.py -v`
Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/services/entity_types.py tests/unit/test_entity_type_prompt_segment.py
git commit -m "feat(services): prompt_segment() builds LLM-facing entity-type bullet list

Used in the next two tasks to replace the hardcoded type lists in
services/discovery.py and pipeline/match/classify.py."
```

---

## Task 6: `CssfDiscoveryService` reads filter IDs and label map from the DB

**Files:**
- Modify: `regwatch/services/cssf_discovery.py:67-86` (delete the dict + helper), `:142-258` (the `run`/`_run_for_cell` block)
- Test: extend `tests/integration/test_cssf_discovery_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_cssf_discovery_service.py`:

```python
def test_run_reads_filter_ids_from_entity_type_table(tmp_path, monkeypatch, httpx_mock):
    """Filter IDs come from EntityType.cssf_entity_filter_id, not config."""
    from regwatch.db.entity_type_seed import seed_default_entity_types
    from regwatch.db.models import EntityType
    from regwatch.services.entity_types import EntityTypeService

    sf = _setup_db(tmp_path)
    with sf() as s:
        seed_default_entity_types(s)
        # Set AIFM's filter ID to a sentinel and verify the scraper requests it.
        svc = EntityTypeService(s)
        aifm = svc.get_by_slug("AIFM")
        svc.update(aifm.entity_type_id, cssf_entity_filter_id=99999)
        s.commit()

    # Empty listing for filter_id=99999, so the run is quick.
    httpx_mock.add_response(
        url=lambda r: "entity_type=99999" in str(r.url),
        html="<html><body>No items</body></html>",
    )
    # (mock detail for any other lookups...)

    from regwatch.services.cssf_discovery import CssfDiscoveryService
    from regwatch.config import CssfDiscoveryConfig, PublicationTypeConfig

    cfg = CssfDiscoveryConfig(
        publication_types=[
            PublicationTypeConfig(label="CSSF circular", filter_id=567, type="CSSF_CIRCULAR")
        ]
    )
    service = CssfDiscoveryService(session_factory=sf, config=cfg)
    run_id = service.run(entity_types=["AIFM"], mode="full", triggered_by="TEST")
    assert run_id > 0
```

(`_setup_db` is the existing helper at the top of `test_cssf_discovery_service.py:29` — already present, no changes needed there yet. In Step 4 below we'll teach it to seed the entity_type table too.)

Also append:

```python
def test_run_skips_slugs_without_filter_id(tmp_path, caplog):
    """A slug with cssf_entity_filter_id=NULL is skipped with INFO log."""
    from regwatch.db.entity_type_seed import seed_default_entity_types
    from regwatch.services.entity_types import EntityTypeService

    sf = _setup_db(tmp_path)
    with sf() as s:
        seed_default_entity_types(s)
        EntityTypeService(s).create(
            slug="PSF_SPECIALISED",
            label="PSF Specialised",
            cssf_entity_filter_id=None,
        )
        s.commit()

    from regwatch.services.cssf_discovery import CssfDiscoveryService
    from regwatch.config import CssfDiscoveryConfig
    cfg = CssfDiscoveryConfig()
    service = CssfDiscoveryService(session_factory=sf, config=cfg)
    import logging
    with caplog.at_level(logging.INFO):
        service.run(entity_types=["PSF_SPECIALISED"], mode="full", triggered_by="TEST")
    assert any(
        "PSF_SPECIALISED" in r.message and "no CSSF filter ID" in r.message
        for r in caplog.records
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_cssf_discovery_service.py::test_run_reads_filter_ids_from_entity_type_table tests/integration/test_cssf_discovery_service.py::test_run_skips_slugs_without_filter_id -v`
Expected: FAIL — calls into `service.run(entity_types=["AIFM"], ...)` pass strings, current signature wants `list[AuthorizationType]`.

- [ ] **Step 3: Refactor `CssfDiscoveryService`**

In `regwatch/services/cssf_discovery.py`:

**3a. Delete the module-level `CSSF_ENTITY_LABEL_TO_AUTH` dict (lines 64-75).** Replace with:

```python
def build_label_map(session: Session) -> dict[str, str]:
    """Substring pattern -> slug, built from EntityType.cssf_detail_labels.

    Patterns are matched case-insensitively as substrings of the
    ``.entities-list li`` text on CSSF detail pages.
    """
    from regwatch.db.models import EntityType  # noqa: PLC0415
    out: dict[str, str] = {}
    for et in session.scalars(
        select(EntityType).where(EntityType.active.is_(True))
    ).all():
        for label in (et.cssf_detail_labels or []):
            out[label] = et.slug
    return out
```

**3b. Replace the existing `_map_labels_to_auth_types` helper (lines 78-86) with:**

```python
def _map_labels_to_slugs(labels: list[str], label_map: dict[str, str]) -> list[str]:
    """Match each label against the prefix mapping; return deduped, sorted slug list."""
    found: set[str] = set()
    for label in labels:
        norm = label.strip()
        for prefix, slug in label_map.items():
            if prefix.lower() in norm.lower():
                found.add(slug)
    return sorted(found)
```

**3c. Change `CssfDiscoveryService.run` signature** (around line 183):

```python
    def run(
        self,
        *,
        entity_types: list[str],   # was: list[AuthorizationType]
        mode: Literal["full", "incremental"],
        triggered_by: str,
        existing_run_id: int | None = None,
        dry_run: bool = False,
        restrict_pub_slug: str | None = None,
    ) -> int:
```

**3d. Inside `run()`, load the EntityType rows once (after the `pubs_to_use = ...` block):**

```python
        from regwatch.db.models import EntityType  # noqa: PLC0415
        with self._sf() as s:
            by_slug: dict[str, EntityType] = {
                et.slug: et
                for et in s.scalars(
                    select(EntityType).where(EntityType.active.is_(True))
                ).all()
            }
```

**3e. Replace the existing loop body** (lines 236-253):

```python
        try:
            for slug in entity_types:
                et_row = by_slug.get(slug)
                if et_row is None:
                    logger.warning("unknown entity slug %s; skipping", slug)
                    continue
                if et_row.cssf_entity_filter_id is None:
                    logger.info(
                        "skipping %s: no CSSF filter ID configured", slug
                    )
                    continue
                entity_filter_id = et_row.cssf_entity_filter_id
                for pub in pubs_to_use:
                    try:
                        self._run_for_cell(run_id, slug, entity_filter_id, pub, mode)
                    except Exception as e:  # noqa: BLE001
                        logger.exception(
                            "cell failed: entity=%s (%d) x content=%s (%d)",
                            slug, entity_filter_id, pub.label, pub.filter_id,
                        )
                        msg = f"{slug} x {pub.label}: {e}"
                        aggregate_error = (
                            f"{aggregate_error}\n{msg}" if aggregate_error else msg
                        )
        finally:
            self._finalize_run(run_id, aggregate_error)
```

**3f. Change `_run_for_cell` and `_reconcile_row`** — replace every `auth_type: AuthorizationType` parameter with `slug: str`, and every `auth_type.value` with `slug`. Replace `_ensure_applicability(s, reg, auth_type)` with `_ensure_applicability(s, reg, slug)`, where the helper's signature becomes `_ensure_applicability(self, s, reg, slug: str)` storing `RegulationApplicability(authorization_type=slug)` directly.

**3g. Update the `entity_types` JSON column writes** (lines 215, 231 — the `DiscoveryRun` row): they already serialize slug strings (`[et.value for et in entity_types]`), so change to `list(entity_types)` (just the slugs).

**3h. Update existing callers (still in this file's docstring/comments only).**

- [ ] **Step 4: Update the two callers of `service.run(entity_types=[...])`**

**`regwatch/web/routes/catalog.py:496-503` block:**

Replace:

```python
    if entity_types:
        auth_types: list[AuthorizationType] = []
        for name in entity_types:
            try:
                auth_types.append(AuthorizationType(name))
            except ValueError:
                pass
    else:
        auth_types = [AuthorizationType(a.type) for a in cfg.entity.authorizations]
```

with:

```python
    if entity_types:
        auth_slugs: list[str] = list(entity_types)
    else:
        auth_slugs = [a.type for a in cfg.entity.authorizations]
```

And change `auth_types` -> `auth_slugs` in the rest of the function. The `DiscoveryRun(entity_types=...)` insert becomes `entity_types=auth_slugs`. The `service.run(entity_types=auth_slugs, ...)` call too. Remove the `AuthorizationType` import from this file's import block.

**`regwatch/cli.py::discover_cssf`:** wherever it builds `AuthorizationType(name)`, replace with the slug string and validate via DB lookup. Look for the call site that maps `--entity` strings into the run.

**`regwatch/main.py:95-176`:** in the scheduled jobs, replace:

```python
auth_types = [
    AuthorizationType(a.type)
    for a in config.entity.authorizations
]
service.run(entity_types=auth_types, ...)
```

with:

```python
auth_slugs = [a.type for a in config.entity.authorizations]
service.run(entity_types=auth_slugs, ...)
```

Remove the `from regwatch.db.models import AuthorizationType` line in the lifespan.

- [ ] **Step 5: Run tests**

Run: `pytest tests/integration/test_cssf_discovery_service.py -v` (after also seeding `entity_type` in the existing `_make_session_factory` helper — that change comes in Task 14, but for now seed it inline in the new tests as shown).
Expected: new tests PASS. Existing tests in this file may FAIL because they pass `AuthorizationType.AIFM` not `"AIFM"` — fix the calls inline (mechanical) and verify they still pass.

Run: `pytest tests/integration/test_cssf_discovery_*.py -v`
Expected: green. If a test fails because it asserts against `auth_type.value`, update it to use the slug string.

- [ ] **Step 6: Commit**

```bash
git add regwatch/services/cssf_discovery.py regwatch/web/routes/catalog.py regwatch/cli.py regwatch/main.py tests/integration/test_cssf_discovery_service.py tests/integration/test_cssf_discovery_*.py
git commit -m "feat(cssf): discovery reads filter IDs and label map from EntityType

CssfDiscoveryService now accepts slug strings instead of the
AuthorizationType enum. Filter IDs and detail-page label patterns
come from the entity_type table — adding a new type via the
upcoming Settings UI immediately reshapes discovery."
```

---

## Task 7: `services/discovery.py` LLM prompt built from `prompt_segment()` at call time

**Files:**
- Modify: `regwatch/services/discovery.py:21-44` (the `_CLASSIFY_SYSTEM` and `_DISCOVER_SYSTEM` constants), `:191-206` (the `_classify_regulation` call)
- Test: append to `tests/unit/test_discovery_service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_discovery_service.py`:

```python
def test_classify_regulation_uses_current_entity_type_registry(session, monkeypatch):
    """Adding a new EntityType row should appear in the next LLM prompt."""
    from unittest.mock import MagicMock

    from regwatch.db.entity_type_seed import seed_default_entity_types
    from regwatch.services.discovery import DiscoveryService
    from regwatch.services.entity_types import EntityTypeService

    seed_default_entity_types(session)
    EntityTypeService(session).create(
        slug="PSF_SPECIALISED",
        label="PSF Specialised",
    )
    # Also create a minimal Regulation to classify.
    from regwatch.db.models import LifecycleStage, Regulation, RegulationType
    reg = Regulation(
        reference_number="TEST/1",
        type=RegulationType.CSSF_CIRCULAR,
        title="Test",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        url="http://x",
        source_of_truth="SEED",
    )
    session.add(reg)
    session.commit()

    llm = MagicMock()
    llm.chat.return_value = '{"is_ict": false, "confidence": 0.9}'
    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog()

    sent_system = llm.chat.call_args.kwargs["system"]
    assert "PSF_SPECIALISED" in sent_system
    assert "AIFM" in sent_system
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_discovery_service.py::test_classify_regulation_uses_current_entity_type_registry -v`
Expected: FAIL — `PSF_SPECIALISED` not in the prompt (it's hardcoded).

- [ ] **Step 3: Refactor the prompt builders**

In `regwatch/services/discovery.py`, replace lines 21-36 (`_CLASSIFY_SYSTEM`):

```python
_CLASSIFY_SYSTEM_TEMPLATE = (
    "You are a regulatory classification expert for Luxembourg financial entities.\n"
    "Given a regulation or circular, determine:\n"
    "1. is_ict: Is this related to ICT, cybersecurity, digital operational resilience, "
    "IT outsourcing, or similar technology risk topics? (true/false)\n"
    "2. dora_pillar: If is_ict is true, which DORA pillar? "
    "(ICT_RISK_MGMT, INCIDENT_REPORTING, RESILIENCE_TESTING, THIRD_PARTY_RISK, "
    "INFO_SHARING, or null)\n"
    "3. applicable_entity_types: Which entity types does this apply to? "
    "(JSON array.)\n"
    "{entity_types}\n"
    "4. is_superseded: Has this been replaced by a newer version? (true/false)\n"
    "5. superseded_by: If superseded, the reference number of the replacement (or null)\n"
    "6. confidence: How confident are you in this classification? (0.0 to 1.0)\n\n"
    "Respond with ONLY a JSON object with these 6 fields."
)
```

And replace `_classify_regulation` (line 191):

```python
    def _classify_regulation(self, reg: Regulation) -> dict | None:
        from regwatch.services.entity_types import prompt_segment  # noqa: PLC0415
        system = _CLASSIFY_SYSTEM_TEMPLATE.format(
            entity_types=prompt_segment(self._session)
        )
        reply = self._llm.chat(
            system=system,
            user=(
                f"Classify this regulation:\n"
                f"Reference: {reg.reference_number}\n"
                f"Title: {reg.title}\n"
                f"Issuing authority: {reg.issuing_authority}\n"
                f"Type: {reg.type.value}"
            ),
        )
        try:
            return extract_json_object(reply)
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM classification failed for %s: %s", reg.reference_number, e)
            return None
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_discovery_service.py -v`
Expected: all PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add regwatch/services/discovery.py tests/unit/test_discovery_service.py
git commit -m "feat(discovery): LLM classifier prompt built from EntityType registry

Adding a new entity type via the Settings UI now reshapes the
classifier's allowed outputs on the next call. No restart required."
```

---

## Task 8: `pipeline/match/classify.py` prompt via `app.state.entity_type_prompt` cache

**Files:**
- Modify: `regwatch/pipeline/match/classify.py:47-74` (the `classify_entity_types` function)
- Modify: `regwatch/main.py` (build the cache at startup, expose refresh hook)
- Modify: `regwatch/pipeline/match/combined.py`, `regwatch/pipeline/pipeline_factory.py` (pass the cached prompt through to the matcher)
- Test: `tests/unit/test_pipeline_classify_entity_types_prompt.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_pipeline_classify_entity_types_prompt.py`:

```python
"""classify_entity_types() consumes an entity-type prompt string at call time."""
from __future__ import annotations

from unittest.mock import MagicMock

from regwatch.pipeline.match.classify import classify_entity_types


def test_classify_entity_types_uses_provided_prompt_segment():
    llm = MagicMock()
    llm.chat.return_value = '["AIFM"]'
    prompt = 'Valid entity_type slugs:\n- "AIFM" (AIFM)\n- "PSF_SPECIALISED" (PSF Specialised)'
    classify_entity_types(
        title="DORA outsourcing",
        text="Operational resilience...",
        llm=llm,
        entity_type_prompt=prompt,
    )
    sent_system = llm.chat.call_args.kwargs["system"]
    assert "PSF_SPECIALISED" in sent_system
    assert "AIFM" in sent_system


def test_classify_entity_types_falls_back_when_no_prompt_passed():
    """Backward compatibility: when no prompt is passed, function still runs."""
    llm = MagicMock()
    llm.chat.return_value = '["AIFM"]'
    result = classify_entity_types(title="x", text="x", llm=llm)
    assert result == ["AIFM"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_pipeline_classify_entity_types_prompt.py -v`
Expected: FAIL — `classify_entity_types` doesn't accept `entity_type_prompt`.

- [ ] **Step 3: Refactor `classify_entity_types`**

In `regwatch/pipeline/match/classify.py`, replace lines 47-74:

```python
_DEFAULT_ENTITY_TYPE_HINT = (
    'Valid entity_type slugs include: "AIFM", "CHAPTER15_MANCO", '
    '"CREDIT_INSTITUTION", "CASP", "INVESTMENT_FIRM", "INSURANCE", '
    '"PENSION_FUND", "ALL". (This list is overridden by the EntityType '
    "registry at runtime — see app.state.entity_type_prompt.)"
)


def classify_entity_types(
    title: str,
    text: str,
    *,
    llm: LLMClient | None = None,
    entity_type_prompt: str | None = None,
) -> list[str] | None:
    """Use the LLM to determine which entity types a document applies to."""
    if llm is None:
        return None
    hint = entity_type_prompt or _DEFAULT_ENTITY_TYPE_HINT
    try:
        reply = llm.chat(
            system=(
                "You analyze regulatory documents to determine which types of "
                "financial entities they apply to. Respond with ONLY a JSON array "
                "of entity type slugs.\n"
                f"{hint}\n"
                "If the document applies broadly to all financial entities, "
                'respond with ["ALL"].'
            ),
            user=(
                f"Which entity types does this document apply to?\n\n"
                f"Title: {title}\nText (first 2000 chars): {text[:2000]}"
            ),
        )
        data = json.loads(reply.strip())
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:  # noqa: BLE001
        logger.warning("LLM entity type classification unavailable")
    return None
```

- [ ] **Step 4: Plumb the prompt through the matcher chain**

In `regwatch/pipeline/match/combined.py`, find the call to `classify_entity_types` (grep first to confirm; it's invoked indirectly). The matcher's constructor accepts an `LLMClient`. Add an optional `entity_type_prompt: str | None = None` field and forward it at the call site.

In `regwatch/pipeline/pipeline_factory.py::build_runner`, after constructing `llm_client` but before constructing the matcher, fetch the prompt from `app.state.entity_type_prompt` if available. Pass it into the matcher constructor.

In `regwatch/main.py`, after the seed block, build the cache:

```python
    from regwatch.services.entity_types import prompt_segment
    with session_factory() as session:
        entity_type_prompt = prompt_segment(session)
    ...
    app.state.entity_type_prompt = entity_type_prompt
```

(Place after `app.state.session_factory = session_factory`.)

The CRUD routes added in Task 17-19 will refresh this cache after mutating writes.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_pipeline_classify_entity_types_prompt.py -v`
Expected: PASS.

Run: `pytest -x --ignore=tests/live`
Expected: still green.

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/match/classify.py regwatch/pipeline/match/combined.py regwatch/pipeline/pipeline_factory.py regwatch/main.py tests/unit/test_pipeline_classify_entity_types_prompt.py
git commit -m "feat(pipeline): classify_entity_types reads prompt from app.state cache

The pipeline matcher has no session in scope at call time, so the
entity-type prompt is built once at startup and cached on app.state.
The Entity Types CRUD routes (later tasks) refresh the cache after
writes."
```

---

## Task 9: Drop the SQLite CHECK constraint on `authorization.type`

**Files:**
- Modify: `regwatch/db/models.py:105-118` (the `Authorization` class)
- Modify: `regwatch/db/migrations.py` (add new function)
- Modify: `regwatch/main.py` (call new migration)
- Test: `tests/unit/test_authorization_type_migration.py` (new)

The current `Authorization.type` column is declared `Enum(AuthorizationType)`, which SQLAlchemy compiles to `VARCHAR(15) CHECK (type IN ('AIFM', 'CHAPTER15_MANCO'))`. Once we change it to `String(20)`, `Base.metadata.create_all` won't ALTER the existing table — the CHECK constraint stays and blocks inserts of new slugs. We need a one-shot SQLite-friendly migration that table-rewrites `authorization` without the CHECK.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_authorization_type_migration.py`:

```python
"""Migration removes the legacy CHECK constraint on authorization.type."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from regwatch.db.migrations import migrate_authorization_type_drop_check


@pytest.fixture
def legacy_db(tmp_path):
    """A SQLite DB with the OLD authorization-type CHECK constraint."""
    db = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE entity (lei VARCHAR(20) PRIMARY KEY, legal_name VARCHAR(255))
        """))
        conn.execute(text("""
            CREATE TABLE authorization (
                authorization_id INTEGER PRIMARY KEY,
                lei VARCHAR(20),
                type VARCHAR(15) CHECK (type IN ('AIFM', 'CHAPTER15_MANCO')),
                cssf_entity_id VARCHAR(20)
            )
        """))
        conn.execute(text(
            "INSERT INTO entity (lei, legal_name) VALUES ('TEST', 'Test')"
        ))
        conn.execute(text(
            "INSERT INTO authorization (lei, type) VALUES ('TEST', 'AIFM')"
        ))
    return engine


def test_legacy_db_rejects_new_slugs_before_migration(legacy_db):
    """Sanity check: the legacy CHECK is actually enforced."""
    with legacy_db.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO authorization (lei, type) VALUES ('TEST', 'PSF_SPECIALISED')"
            ))


def test_migration_drops_check_and_preserves_data(legacy_db):
    migrate_authorization_type_drop_check(legacy_db)
    with legacy_db.begin() as conn:
        rows = conn.execute(text("SELECT lei, type FROM authorization")).all()
        assert rows == [("TEST", "AIFM")]
        # And new slugs now insert successfully.
        conn.execute(text(
            "INSERT INTO authorization (lei, type) VALUES ('TEST', 'PSF_SPECIALISED')"
        ))


def test_migration_idempotent_on_clean_db(tmp_path):
    """Running on a DB without the legacy CHECK is a no-op."""
    db = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE authorization (
                authorization_id INTEGER PRIMARY KEY,
                lei VARCHAR(20),
                type VARCHAR(20),
                cssf_entity_id VARCHAR(20)
            )
        """))
        conn.execute(text(
            "INSERT INTO authorization (lei, type) VALUES ('X', 'PSF')"
        ))
    migrate_authorization_type_drop_check(engine)
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT type FROM authorization")).all()
        assert rows == [("PSF",)]


def test_migration_no_table_is_no_op(tmp_path):
    db = tmp_path / "empty.db"
    engine = create_engine(f"sqlite:///{db}")
    migrate_authorization_type_drop_check(engine)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_authorization_type_migration.py -v`
Expected: `ImportError: cannot import name 'migrate_authorization_type_drop_check'`.

- [ ] **Step 3: Write the migration**

Append to `regwatch/db/migrations.py`:

```python
def migrate_authorization_type_drop_check(engine: Engine) -> None:
    """Remove the legacy CHECK(type IN ('AIFM','CHAPTER15_MANCO')) constraint on
    authorization.type so new entity-type slugs can be inserted.

    SQLite has no DROP CONSTRAINT — we use the table-rewrite pattern:
    rename old table, create new (without CHECK), copy rows, drop old.

    Idempotent: returns cleanly if the table doesn't exist or the
    constraint is already gone.
    """
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='authorization'"
        )).first()
        if row is None:
            return  # fresh DB; create_all handles it
        ddl = (row[0] or "")
        if "CHECK" not in ddl.upper() or "AIFM" not in ddl.upper():
            return  # already migrated or never had the constraint

        logger.info("Migrating authorization table to drop legacy type CHECK")

        # Capture column list so the INSERT SELECT below copies every column
        # (a future column add would otherwise be silently dropped).
        col_rows = conn.execute(text("PRAGMA table_info(authorization)")).all()
        cols = [r[1] for r in col_rows]
        col_list = ", ".join(cols)

        conn.execute(text(
            "ALTER TABLE authorization RENAME TO _authorization_old"
        ))
        # Recreate the table with the canonical (no-CHECK) shape.
        # We intentionally hand-write the DDL rather than relying on
        # Base.metadata so this migration works against any future model
        # tweak; the *intent* is to drop a constraint, not refresh schema.
        conn.execute(text("""
            CREATE TABLE authorization (
                authorization_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                lei VARCHAR(20) NOT NULL,
                type VARCHAR(20) NOT NULL,
                cssf_entity_id VARCHAR(20),
                authorization_date DATE,
                status VARCHAR(50),
                cssf_url VARCHAR(500),
                FOREIGN KEY(lei) REFERENCES entity (lei),
                CONSTRAINT uq_authorization_lei_type UNIQUE (lei, type)
            )
        """))
        conn.execute(text(
            f"INSERT INTO authorization ({col_list}) "
            f"SELECT {col_list} FROM _authorization_old"
        ))
        conn.execute(text("DROP TABLE _authorization_old"))
        logger.info("authorization table migrated; CHECK constraint dropped")
```

- [ ] **Step 4: Change the model**

In `regwatch/db/models.py`, replace line 110:

```python
    type: Mapped[AuthorizationType] = mapped_column(Enum(AuthorizationType))
```

with:

```python
    type: Mapped[str] = mapped_column(String(20))
```

(Don't delete the `AuthorizationType` enum yet — Task 10 does that. Other consumers still import it.)

- [ ] **Step 5: Wire the migration into startup**

In `regwatch/main.py`, change the import block at line 65-68 from:

```python
    from regwatch.db.migrations import (
        migrate_discovery_run_item_columns,
        migrate_regulation_created_at,
    )
    migrate_regulation_created_at(engine)
```

to:

```python
    from regwatch.db.migrations import (
        migrate_authorization_type_drop_check,
        migrate_discovery_run_item_columns,
        migrate_regulation_created_at,
    )
    migrate_authorization_type_drop_check(engine)
    migrate_regulation_created_at(engine)
```

Order matters: this migration runs BEFORE `sync_schema` and `create_virtual_tables`, just like the others.

- [ ] **Step 6: Run tests**

Run: `pytest tests/unit/test_authorization_type_migration.py -v`
Expected: 4 PASS.

Run: `pytest -x --ignore=tests/live`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add regwatch/db/migrations.py regwatch/db/models.py regwatch/main.py tests/unit/test_authorization_type_migration.py
git commit -m "feat(db): migrate authorization.type from Enum to String(20)

SQLite has no DROP CONSTRAINT, so we table-rewrite. Idempotent and
column-aware so any future authorization column add isn't silently
dropped on second-boot."
```

---

## Task 10: Delete the `AuthorizationType` enum and `Literal`

**Files:**
- Modify: `regwatch/db/models.py:50-52` (delete)
- Modify: `regwatch/config.py:10-15` (delete Literal, change AuthorizationConfig.type)
- Modify: `regwatch/services/regulations.py:22` (Literal -> str)
- Modify: `regwatch/web/routes/catalog.py:79-83` (drop the cast)
- Modify: `regwatch/rag/retrieval.py:29` (comment update)
- Modify: `tests/unit/test_db_models.py` (drop enum import)
- Modify: every remaining file that imports `AuthorizationType` (likely already removed in Task 6; this is the safety sweep)

- [ ] **Step 1: Find all remaining references**

Run: `grep -rn 'AuthorizationType' regwatch tests`
Expected: only legitimate string references should remain — `AuthorizationType` the type symbol should appear zero times after this task.

- [ ] **Step 2: Delete the enum and Literal**

`regwatch/db/models.py` — delete lines 50-52:

```python
class AuthorizationType(StrEnum):
    AIFM = "AIFM"
    CHAPTER15_MANCO = "CHAPTER15_MANCO"
```

`regwatch/config.py` — delete line 10 (`AuthorizationType = Literal[...]`) and change the `AuthorizationConfig.type` field from `AuthorizationType` to `str`:

```python
class AuthorizationConfig(BaseModel):
    type: str
    cssf_entity_id: str
```

Remove the `Literal` import if no longer used.

- [ ] **Step 3: Sweep remaining import sites**

For each file listed above, remove `AuthorizationType` from imports and replace its uses with `str`:

- `regwatch/services/regulations.py:22`: change
  ```python
  authorization_type: Literal["AIFM", "CHAPTER15_MANCO"] | None = None
  ```
  to
  ```python
  authorization_type: str | None = None
  ```
- `regwatch/web/routes/catalog.py:79-83`: delete the cast block. Replace with:
  ```python
  auth_value: str | None = authorization or None
  ```
- `regwatch/rag/retrieval.py:29`: update the docstring comment from `"AIFM" or "CHAPTER15_MANCO"` to "entity-type slug" (no code change).
- `tests/unit/test_db_models.py`: drop the `AuthorizationType` import; if any test enumerates members, replace with the seeded EntityType rows fetched at test time.

- [ ] **Step 4: Run the suite**

Run: `pytest -x --ignore=tests/live`
Expected: green. Any failures point to a missed reference.

Run: `mypy regwatch`
Expected: no errors. (This is the win — strict typing survives.)

Run: `ruff check regwatch`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add regwatch/db/models.py regwatch/config.py regwatch/services/regulations.py regwatch/web/routes/catalog.py regwatch/rag/retrieval.py tests/unit/test_db_models.py
git commit -m "refactor: delete AuthorizationType enum + Literal — slugs are strings now

Every consumer migrated in earlier tasks. The enum had two values
and was the bottleneck blocking pluggable types; with it gone, the
entire entity-type universe lives in the EntityType table."
```

---

## Task 11: Render context — inject `entity_types` and `active_entity_type`

**Files:**
- Modify: `regwatch/web/templates_context.py`
- Test: `tests/integration/test_active_entity_type_cookie.py` (new — assertions on context)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_active_entity_type_cookie.py`:

```python
"""Render context injects entity_types and active_entity_type."""
from __future__ import annotations

from tests.integration.test_app_smoke import _client


def test_render_page_injects_entity_types(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/catalog")
    assert r.status_code in (200, 303)
    if r.status_code == 200:
        # The sidebar's data-driven switcher shows both seeded slugs.
        assert "AIFM" in r.text
        assert "CHAPTER15_MANCO" in r.text


def test_active_entity_type_cookie_filters_catalog(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        # Without cookie or query param -> "All": catalog renders without filter.
        r1 = client.get("/catalog")
        assert r1.status_code == 200

        # Set the cookie via the dedicated route.
        r2 = client.post(
            "/settings/active-entity-type",
            data={"entity_type": "AIFM"},
            follow_redirects=False,
        )
        assert r2.status_code == 303
        assert "active_entity_type=AIFM" in r2.headers.get("set-cookie", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_active_entity_type_cookie.py -v`
Expected: first test may pass partially (slugs aren't yet in template); second test FAIL (route doesn't exist yet — Task 13).

- [ ] **Step 3: Inject context**

Update `regwatch/web/templates_context.py`:

```python
"""Render helper that auto-injects sidebar_badges, entity_types, and
active_entity_type into full-page renders."""
from __future__ import annotations

from typing import Any

from fastapi import Request

from regwatch.services.entity_types import EntityTypeService
from regwatch.services.sidebar_badges import SidebarBadgeService


def render_page(
    request: Request,
    template_name: str,
    context: dict[str, Any],
) -> Any:
    templates = request.app.state.templates
    extras: dict[str, Any] = {}
    if "sidebar_badges" not in context:
        with request.app.state.session_factory() as session:
            extras["sidebar_badges"] = SidebarBadgeService(session).counts()
    if "entity_types" not in context:
        with request.app.state.session_factory() as session:
            extras["entity_types"] = EntityTypeService(session).list_active()
    if "active_entity_type" not in context:
        extras["active_entity_type"] = request.cookies.get("active_entity_type", "") or ""
    final_context = {**extras, **context}
    return templates.TemplateResponse(request, template_name, final_context)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_active_entity_type_cookie.py::test_render_page_injects_entity_types -v`
Expected: PASS (test 2 still fails — that's Task 13).

- [ ] **Step 5: Commit**

```bash
git add regwatch/web/templates_context.py tests/integration/test_active_entity_type_cookie.py
git commit -m "feat(web): render_page injects entity_types and active_entity_type

Templates can now render the global switcher and per-page dropdowns
from a single source of truth."
```

---

## Task 12: Sidebar global "Viewing" switcher

**Files:**
- Modify: `regwatch/web/templates/partials/sidebar.html`
- Test: append to `tests/integration/test_active_entity_type_cookie.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_active_entity_type_cookie.py`:

```python
def test_sidebar_shows_global_switcher(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/catalog")
    assert r.status_code == 200
    # The switcher form posts to the cookie route.
    assert 'action="/settings/active-entity-type"' in r.text
    # "All entity types" is the default option.
    assert "All entity types" in r.text
    # Hardcoded sidebar links are gone.
    assert '/catalog?authorization=AIFM"' not in r.text
    assert '/catalog?authorization=CHAPTER15_MANCO"' not in r.text


def test_sidebar_marks_active_option_when_cookie_set(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.cookies.set("active_entity_type", "AIFM")
        r = client.get("/catalog")
    assert 'value="AIFM" selected' in r.text or 'selected value="AIFM"' in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_active_entity_type_cookie.py -v -k sidebar`
Expected: FAIL — hardcoded links still there.

- [ ] **Step 3: Update the sidebar template**

Replace `regwatch/web/templates/partials/sidebar.html` entirely:

```html
{% macro badge(count) -%}
  {% if count %}
    <span class="ml-2 inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5
                 bg-amber-500 text-white text-xs font-semibold rounded-full shrink-0">
      {{ count if count < 100 else '99+' }}
    </span>
  {% endif %}
{%- endmacro %}

{% set sb = sidebar_badges|default(None) %}
{% set ets = entity_types|default([]) %}
{% set active_et = active_entity_type|default('') %}

<aside class="w-56 bg-slate-900 text-slate-100 min-h-screen p-4 flex flex-col">
  <div class="text-xl font-bold mb-4">RegWatch</div>

  <form method="post" action="/settings/active-entity-type" class="mb-6">
    <label class="text-xs uppercase text-slate-400">Viewing</label>
    <select name="entity_type" onchange="this.form.submit()"
            class="w-full bg-slate-800 text-slate-100 text-sm rounded px-2 py-1 mt-1">
      <option value="" {% if not active_et %}selected{% endif %}>All entity types</option>
      {% for et in ets %}
        <option value="{{ et.slug }}" {% if active_et == et.slug %}selected{% endif %}>{{ et.label }}</option>
      {% endfor %}
    </select>
  </form>

  <nav class="flex flex-col gap-1 text-sm">
    <a href="/" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'dashboard' %}bg-slate-800{% endif %}">Dashboard</a>
    <a href="/inbox" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'inbox' %}bg-slate-800{% endif %}">
      <span>Inbox</span>{{ badge(sb.inbox if sb else 0) }}
    </a>
    <a href="/catalog" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'catalog' %}bg-slate-800{% endif %}">
      <span>Catalog</span>{{ badge(sb.catalog if sb else 0) }}
    </a>
    <a href="/ict" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'ict' %}bg-slate-800{% endif %}">
      <span>ICT / DORA</span>{{ badge(sb.ict if sb else 0) }}
    </a>
    <a href="/drafts" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'drafts' %}bg-slate-800{% endif %}">
      <span>Drafts</span>{{ badge(sb.drafts if sb else 0) }}
    </a>
    <a href="/deadlines" class="px-3 py-2 rounded hover:bg-slate-800 flex items-center justify-between {% if active == 'deadlines' %}bg-slate-800{% endif %}">
      <span>Deadlines</span>{{ badge(sb.deadlines if sb else 0) }}
    </a>
    <a href="/chat" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'chat' %}bg-slate-800{% endif %}">Q&amp;A</a>
    <a href="/settings" class="mt-auto px-3 py-2 rounded hover:bg-slate-800 {% if active == 'settings' %}bg-slate-800{% endif %}">Settings</a>
    <a href="/settings/extraction" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">Extraction Fields</a>
    <a href="/settings/schedules" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">Schedules</a>
    <a href="/settings/entity-types" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">Entity Types</a>
  </nav>
</aside>
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_active_entity_type_cookie.py -v -k sidebar`
Expected: both sidebar tests PASS. The `test_active_entity_type_cookie_filters_catalog` still fails (cookie route doesn't exist) — Task 13 handles it.

- [ ] **Step 5: Commit**

```bash
git add regwatch/web/templates/partials/sidebar.html tests/integration/test_active_entity_type_cookie.py
git commit -m "feat(web): global 'Viewing' switcher in the sidebar

Replaces the two hardcoded AIFM/Chapter-15 links with a single
data-driven dropdown plus a new 'Entity Types' link under Settings."
```

---

## Task 13: `POST /settings/active-entity-type` cookie route

**Files:**
- Create: `regwatch/web/routes/entity_types.py` (the route file — fleshed out in later tasks)
- Modify: `regwatch/main.py` (register the router)

- [ ] **Step 1: The test was already written in Task 11/12.**

Run: `pytest tests/integration/test_active_entity_type_cookie.py::test_active_entity_type_cookie_filters_catalog -v`
Expected: FAIL — 404 on POST.

- [ ] **Step 2: Create the route file**

Create `regwatch/web/routes/entity_types.py`:

```python
"""Entity-type registry CRUD + the global 'active entity type' cookie route."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/settings", tags=["entity_types"])

_COOKIE = "active_entity_type"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


@router.post("/active-entity-type")
def set_active_entity_type(
    request: Request,
    entity_type: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Persist the user's sidebar switcher selection in a cookie.

    Empty string = 'All entity types' (clears the cookie).
    """
    referer = request.headers.get("referer", "/")
    response = RedirectResponse(url=referer, status_code=303)
    if entity_type:
        response.set_cookie(
            _COOKIE,
            entity_type,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
    else:
        response.delete_cookie(_COOKIE)
    return response
```

- [ ] **Step 3: Register the router**

In `regwatch/main.py`, around line 280 (where the other route imports live), add:

```python
    from regwatch.web.routes import (
        entity_types as entity_types_routes,
    )
```

And around line 306 (where `app.include_router(...)` is called), add:

```python
    app.include_router(entity_types_routes.router)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_active_entity_type_cookie.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/web/routes/entity_types.py regwatch/main.py
git commit -m "feat(web): POST /settings/active-entity-type sets the sidebar cookie

Cookie roundtrip lets the user pick an entity type once in the
sidebar; every page that supports filtering reads it next request."
```

---

## Task 14: Catalog dropdown — data-driven + cookie sync

**Files:**
- Modify: `regwatch/web/templates/catalog/list.html:23-27`
- Modify: `regwatch/web/routes/catalog.py` (the `catalog` GET handler — read cookie when no `authorization` query param; write cookie when set)
- Modify: `tests/integration/test_active_entity_type_cookie.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_active_entity_type_cookie.py`:

```python
def test_catalog_dropdown_renders_from_db(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        # Add a third entity type — it should appear in the dropdown.
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            EntityTypeService(s).create(slug="PSF_SPECIALISED", label="PSF Specialised")
            s.commit()
        r = client.get("/catalog")
    assert r.status_code == 200
    assert "PSF Specialised" in r.text


def test_catalog_cookie_filters_when_no_query_param(tmp_path, monkeypatch):
    """A bare /catalog?... visit (with a non-empty query, no authorization) uses the cookie."""
    with _client(tmp_path, monkeypatch) as client:
        # Seed a regulation for AIFM only.
        with client.app.state.session_factory() as s:
            from regwatch.db.models import (
                LifecycleStage, Regulation, RegulationApplicability, RegulationType,
            )
            reg = Regulation(
                reference_number="AIFM-ONLY",
                type=RegulationType.CSSF_CIRCULAR,
                title="AIFM only",
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                url="http://x",
                source_of_truth="SEED",
            )
            s.add(reg)
            s.flush()
            s.add(RegulationApplicability(
                regulation_id=reg.regulation_id, authorization_type="AIFM"
            ))
            s.commit()
        client.cookies.set("active_entity_type", "CHAPTER15_MANCO")
        # No "authorization=" in URL but cookie set: AIFM-only reg should be hidden.
        r = client.get("/catalog?lifecycle=IN_FORCE")
    assert "AIFM-ONLY" not in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_active_entity_type_cookie.py -v -k catalog`
Expected: cookie filter test FAILs — query param block in the route doesn't consider cookies.

- [ ] **Step 3: Update the template**

In `regwatch/web/templates/catalog/list.html`, replace lines 23-27:

```jinja
    <select name="authorization" class="border rounded px-2 py-1" onchange="this.form.submit()">
      <option value="">Any authorisation</option>
      {% for et in entity_types %}
      <option value="{{ et.slug }}" {% if flt.authorization_type == et.slug %}selected{% endif %}>{{ et.label }}</option>
      {% endfor %}
    </select>
```

- [ ] **Step 4: Update the route**

In `regwatch/web/routes/catalog.py`, modify the `catalog` handler. Replace the `auth_value` block (lines 78-83) with:

```python
    # Resolve the active entity type:
    # 1. Explicit `?authorization=X` wins (empty string = "All").
    # 2. Otherwise, read the sidebar cookie.
    # 3. Otherwise, no filter.
    if authorization is not None:
        auth_value: str | None = authorization or None
        cookie_value_to_set: str | None = authorization
    else:
        cookie_value = request.cookies.get("active_entity_type", "")
        auth_value = cookie_value or None
        cookie_value_to_set = None  # don't rewrite the cookie on cookie-driven reads
```

After the response is built (just before `return response`), add the cookie sync:

```python
    if cookie_value_to_set is not None:
        if cookie_value_to_set:
            response.set_cookie(
                "active_entity_type", cookie_value_to_set,
                max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
            )
        else:
            response.delete_cookie("active_entity_type")
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/integration/test_active_entity_type_cookie.py -v`
Expected: all PASS.

Run: `pytest tests/integration/test_app_smoke.py -v`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/templates/catalog/list.html regwatch/web/routes/catalog.py tests/integration/test_active_entity_type_cookie.py
git commit -m "feat(catalog): data-driven dropdown + sidebar cookie fallback

Catalog now reads ?authorization=X if present, otherwise the
active_entity_type cookie. Selecting a per-page filter also writes
the cookie so global state stays in sync."
```

---

## Task 15: Inbox dropdown — data-driven + cookie sync

**Files:**
- Modify: `regwatch/web/templates/inbox/list.html:25-29`
- Modify: `regwatch/web/routes/inbox.py`
- Test: append to `tests/integration/test_active_entity_type_cookie.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_inbox_dropdown_renders_from_db(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            EntityTypeService(s).create(slug="PSF_SUPPORT", label="PSF Support")
            s.commit()
        r = client.get("/inbox")
    assert r.status_code == 200
    assert "PSF Support" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_active_entity_type_cookie.py::test_inbox_dropdown_renders_from_db -v`
Expected: FAIL — template still has hardcoded options.

- [ ] **Step 3: Update the template**

In `regwatch/web/templates/inbox/list.html`, replace lines 25-29:

```jinja
      <select name="entity_type" onchange="this.form.submit()" class="border rounded px-2 py-1 text-sm">
        <option value="">All (relevant)</option>
        {% for et in entity_types %}
        <option value="{{ et.slug }}" {% if current_entity_type == et.slug %}selected{% endif %}>{{ et.label }}</option>
        {% endfor %}
      </select>
```

- [ ] **Step 4: Update the route**

In `regwatch/web/routes/inbox.py::inbox_list`, replace lines 15-19 with:

```python
def inbox_list(
    request: Request,
    source: str | None = None,
    entity_type: str | None = None,
    show_all: bool = False,
) -> HTMLResponse:
    # Cookie fallback: if no explicit ?entity_type=X, read the sidebar selection.
    if entity_type is None:
        cookie_value = request.cookies.get("active_entity_type", "")
        effective_entity_type = cookie_value or None
    else:
        effective_entity_type = entity_type or None
```

Then change the `InboxService.list_new(entity_type=entity_type, ...)` call to `entity_type=effective_entity_type`, and `current_entity_type=entity_type` in the template context to `current_entity_type=effective_entity_type or ""`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/integration/test_active_entity_type_cookie.py -v`
Expected: green.

Run: full suite to catch regressions.

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/templates/inbox/list.html regwatch/web/routes/inbox.py tests/integration/test_active_entity_type_cookie.py
git commit -m "feat(inbox): data-driven entity-type dropdown + cookie fallback"
```

---

## Task 16: GET `/settings/entity-types` listing page

**Files:**
- Modify: `regwatch/web/routes/entity_types.py` (add GET)
- Create: `regwatch/web/templates/settings/entity_types.html`
- Create: `regwatch/web/templates/settings/_entity_type_row.html`
- Test: `tests/integration/test_entity_type_routes.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_entity_type_routes.py`:

```python
"""HTTP route tests for the Entity Types Settings page."""
from __future__ import annotations

from tests.integration.test_app_smoke import _client


def test_listing_page_renders(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.get("/settings/entity-types")
    assert r.status_code == 200
    assert "Entity Types" in r.text
    # Both seeded rows are visible.
    assert "AIFM" in r.text
    assert "CHAPTER15_MANCO" in r.text
    # The add form is reachable.
    assert "Add entity type" in r.text


def test_listing_page_separates_active_from_hidden(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            svc = EntityTypeService(s)
            aifm = svc.get_by_slug("AIFM")
            svc.deactivate(aifm.entity_type_id)
            s.commit()
        r = client.get("/settings/entity-types")
    assert r.status_code == 200
    assert "Hidden" in r.text  # the hidden-rows heading appears
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_entity_type_routes.py -v`
Expected: 404.

- [ ] **Step 3: Add the GET route**

Append to `regwatch/web/routes/entity_types.py`:

```python
from fastapi.responses import HTMLResponse

from regwatch.services.entity_types import EntityTypeService
from regwatch.web.templates_context import render_page


@router.get("/entity-types", response_class=HTMLResponse)
def entity_types_list(request: Request) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        rows = EntityTypeService(session).list_all()
    return render_page(
        request,
        "settings/entity_types.html",
        {
            "active": "settings",
            "active_rows": [r for r in rows if r.active],
            "hidden_rows": [r for r in rows if not r.active],
        },
    )
```

- [ ] **Step 4: Create the listing template**

Create `regwatch/web/templates/settings/entity_types.html`:

```jinja
{% extends "base.html" %}
{% block title %}RegWatch — Entity Types{% endblock %}
{% block content %}
  <h1 class="text-2xl font-bold mb-4">Entity Types</h1>
  <p class="text-sm text-slate-600 mb-4">
    Entity types drive the sidebar switcher, catalog &amp; inbox filters,
    CSSF discovery filter IDs, and LLM classifier prompts. Add a new
    type to start monitoring its regulations — no restart required.
  </p>

  <table class="w-full bg-white border rounded shadow-sm text-sm mb-4">
    <thead class="bg-slate-100 text-xs uppercase text-slate-600">
      <tr>
        <th class="text-left p-2">Slug</th>
        <th class="text-left p-2">Label</th>
        <th class="text-left p-2">CSSF filter ID</th>
        <th class="text-left p-2">Sort</th>
        <th class="text-left p-2">Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for et in active_rows %}
        {% include "settings/_entity_type_row.html" %}
      {% endfor %}
    </tbody>
  </table>

  <details class="mb-6">
    <summary class="cursor-pointer text-sm text-slate-600">
      Add entity type
    </summary>
    <form method="post" action="/settings/entity-types" class="grid grid-cols-2 gap-2 mt-3 text-sm">
      <label class="col-span-1">Slug
        <input name="slug" required pattern="[A-Z][A-Z0-9_]{1,38}[A-Z0-9]"
               class="border rounded px-2 py-1 w-full" placeholder="PSF_SPECIALISED">
      </label>
      <label class="col-span-1">Label
        <input name="label" required class="border rounded px-2 py-1 w-full"
               placeholder="PSF — Specialised">
      </label>
      <label class="col-span-1">CSSF filter ID
        <input name="cssf_entity_filter_id" type="number"
               class="border rounded px-2 py-1 w-full" placeholder="(optional)">
      </label>
      <label class="col-span-1">Sort order
        <input name="sort_order" type="number" value="100"
               class="border rounded px-2 py-1 w-full">
      </label>
      <label class="col-span-2">CSSF detail-page labels (comma-separated)
        <input name="cssf_detail_labels" class="border rounded px-2 py-1 w-full"
               placeholder="Specialised PSF, PSF spécialisé">
      </label>
      <button class="col-span-2 px-3 py-1 bg-emerald-600 text-white rounded">
        Add entity type
      </button>
    </form>
  </details>

  {% if hidden_rows %}
    <h2 class="text-lg font-semibold mt-6 mb-2">Hidden</h2>
    <table class="w-full bg-white border rounded shadow-sm text-sm opacity-60">
      <tbody>
        {% for et in hidden_rows %}
          {% include "settings/_entity_type_row.html" %}
        {% endfor %}
      </tbody>
    </table>
  {% endif %}
{% endblock %}
```

Create `regwatch/web/templates/settings/_entity_type_row.html`:

```jinja
<tr id="et-row-{{ et.entity_type_id }}">
  <td class="p-2 font-mono">{{ et.slug }}</td>
  <td class="p-2">{{ et.label }}</td>
  <td class="p-2">{{ et.cssf_entity_filter_id or '—' }}</td>
  <td class="p-2">{{ et.sort_order }}</td>
  <td class="p-2 space-x-1">
    {% if et.active %}
      <form method="post" action="/settings/entity-types/{{ et.entity_type_id }}/deactivate" class="inline">
        <button class="text-xs text-amber-700 hover:underline">Deactivate</button>
      </form>
    {% else %}
      <form method="post" action="/settings/entity-types/{{ et.entity_type_id }}/reactivate" class="inline">
        <button class="text-xs text-emerald-700 hover:underline">Reactivate</button>
      </form>
    {% endif %}
  </td>
</tr>
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/integration/test_entity_type_routes.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/routes/entity_types.py regwatch/web/templates/settings/entity_types.html regwatch/web/templates/settings/_entity_type_row.html tests/integration/test_entity_type_routes.py
git commit -m "feat(settings): GET /settings/entity-types listing page

Read-only view of the registry. Add and toggle actions live in the
next tasks."
```

---

## Task 17: POST `/settings/entity-types` — add

**Files:**
- Modify: `regwatch/web/routes/entity_types.py`
- Test: append to `tests/integration/test_entity_type_routes.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_add_entity_type_happy_path(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/settings/entity-types",
            data={
                "slug": "PSF_SPECIALISED",
                "label": "PSF Specialised",
                "cssf_entity_filter_id": "1234",
                "sort_order": "30",
                "cssf_detail_labels": "Specialised PSF, PSF spécialisé",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == "/settings/entity-types"

    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            row = EntityTypeService(s).get_by_slug("PSF_SPECIALISED")
        assert row is not None
        assert row.cssf_entity_filter_id == 1234
        assert row.cssf_detail_labels == ["Specialised PSF", "PSF spécialisé"]


def test_add_rejects_bad_slug(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/settings/entity-types",
            data={"slug": "lower_case", "label": "x"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "slug-invalid" in r.headers["location"]


def test_add_rejects_duplicate_slug(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/settings/entity-types",
            data={"slug": "AIFM", "label": "duplicate"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "slug-conflict" in r.headers["location"]


def test_add_refreshes_app_state_prompt_cache(tmp_path, monkeypatch):
    """After adding a type, app.state.entity_type_prompt reflects it."""
    with _client(tmp_path, monkeypatch) as client:
        client.post(
            "/settings/entity-types",
            data={"slug": "PSF_SUPPORT", "label": "PSF Support"},
            follow_redirects=False,
        )
        assert "PSF_SUPPORT" in client.app.state.entity_type_prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_entity_type_routes.py -v -k add`
Expected: 404.

- [ ] **Step 3: Add the POST handler**

Append to `regwatch/web/routes/entity_types.py`:

```python
from regwatch.services.entity_types import (
    InvalidSlugError,
    SlugConflictError,
    prompt_segment,
)


@router.post("/entity-types")
def entity_types_add(
    request: Request,
    slug: Annotated[str, Form()],
    label: Annotated[str, Form()],
    cssf_entity_filter_id: Annotated[str, Form()] = "",
    cssf_detail_labels: Annotated[str, Form()] = "",
    sort_order: Annotated[int, Form()] = 100,
) -> RedirectResponse:
    parsed_filter_id: int | None
    if cssf_entity_filter_id.strip():
        try:
            parsed_filter_id = int(cssf_entity_filter_id)
        except ValueError:
            return RedirectResponse(
                "/settings/entity-types?error=filter-id-not-int", status_code=303
            )
    else:
        parsed_filter_id = None

    parsed_labels: list[str] | None
    if cssf_detail_labels.strip():
        parsed_labels = [
            chunk.strip()
            for chunk in cssf_detail_labels.split(",")
            if chunk.strip()
        ] or None
    else:
        parsed_labels = None

    with request.app.state.session_factory() as session:
        svc = EntityTypeService(session)
        try:
            svc.create(
                slug=slug.strip(),
                label=label.strip(),
                cssf_entity_filter_id=parsed_filter_id,
                cssf_detail_labels=parsed_labels,
                sort_order=sort_order,
            )
            session.commit()
            request.app.state.entity_type_prompt = prompt_segment(session)
        except InvalidSlugError:
            return RedirectResponse(
                "/settings/entity-types?error=slug-invalid", status_code=303
            )
        except SlugConflictError:
            return RedirectResponse(
                "/settings/entity-types?error=slug-conflict", status_code=303
            )

    return RedirectResponse("/settings/entity-types", status_code=303)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_entity_type_routes.py -v -k add`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/web/routes/entity_types.py tests/integration/test_entity_type_routes.py
git commit -m "feat(settings): POST /settings/entity-types adds a new type

Includes slug-regex and uniqueness validation; refreshes the
app.state.entity_type_prompt cache so the matcher sees the new
type on the very next pipeline run."
```

---

## Task 18: POST `/settings/entity-types/{id}/deactivate` and `/reactivate`

**Files:**
- Modify: `regwatch/web/routes/entity_types.py`
- Test: append to `tests/integration/test_entity_type_routes.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_deactivate_hides_from_active_list(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            aifm_id = EntityTypeService(s).get_by_slug("AIFM").entity_type_id
        r = client.post(
            f"/settings/entity-types/{aifm_id}/deactivate",
            follow_redirects=False,
        )
    assert r.status_code == 303

    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            active = [r.slug for r in EntityTypeService(s).list_active()]
        assert "AIFM" not in active


def test_reactivate(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            svc = EntityTypeService(s)
            aifm_id = svc.get_by_slug("AIFM").entity_type_id
            svc.deactivate(aifm_id)
            s.commit()
        r = client.post(
            f"/settings/entity-types/{aifm_id}/reactivate",
            follow_redirects=False,
        )
    assert r.status_code == 303

    with _client(tmp_path, monkeypatch) as client:
        with client.app.state.session_factory() as s:
            from regwatch.services.entity_types import EntityTypeService
            active = [r.slug for r in EntityTypeService(s).list_active()]
        assert "AIFM" in active
```

- [ ] **Step 2: Run tests**

Expected: FAIL — routes don't exist.

- [ ] **Step 3: Add the handlers**

Append to `regwatch/web/routes/entity_types.py`:

```python
@router.post("/entity-types/{entity_type_id}/deactivate")
def entity_types_deactivate(
    request: Request, entity_type_id: int
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        EntityTypeService(session).deactivate(entity_type_id)
        session.commit()
        request.app.state.entity_type_prompt = prompt_segment(session)
    return RedirectResponse("/settings/entity-types", status_code=303)


@router.post("/entity-types/{entity_type_id}/reactivate")
def entity_types_reactivate(
    request: Request, entity_type_id: int
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        EntityTypeService(session).reactivate(entity_type_id)
        session.commit()
        request.app.state.entity_type_prompt = prompt_segment(session)
    return RedirectResponse("/settings/entity-types", status_code=303)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/integration/test_entity_type_routes.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/web/routes/entity_types.py tests/integration/test_entity_type_routes.py
git commit -m "feat(settings): deactivate/reactivate routes for entity types

Soft-delete only — existing RegulationApplicability rows that
reference the slug stay intact and reappear on reactivation."
```

---

## Task 19: Shared `seeded_entity_types` test fixture and CLI / smoke updates

**Files:**
- Modify: `tests/conftest.py` (add fixture)
- Modify: `regwatch/cli.py::discover_cssf` (drop AuthorizationType use; validate slug against DB)
- Modify: `tests/integration/test_cli_discover_cssf.py` (update assertions)
- Modify: `config.example.yaml` (remove `entity_filter_ids` block)

- [ ] **Step 1: Add the conftest fixture**

In `tests/conftest.py` (or create if missing):

```python
"""Shared pytest fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture
def seeded_entity_types(tmp_path):
    """Yield a session_factory tied to a fresh DB with the two default entity types seeded.

    Use when a test exercises CSSF discovery / classification but does NOT
    boot the full FastAPI app (which would seed automatically).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from regwatch.db.entity_type_seed import seed_default_entity_types
    from regwatch.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'fixture.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()
    return sf
```

- [ ] **Step 2: Update CLI `--entity` flag**

In `regwatch/cli.py::discover_cssf`, find the block that maps `--entity` strings into `AuthorizationType` (likely near line 350-400; grep `AuthorizationType` in this file). Replace with:

```python
        with session_factory() as s:
            valid_slugs = {et.slug for et in EntityTypeService(s).list_active()}
        if entity:
            invalid = [e for e in entity if e not in valid_slugs]
            if invalid:
                typer.echo(
                    f"Unknown entity slug(s): {invalid}. Active slugs: {sorted(valid_slugs)}",
                    err=True,
                )
                raise typer.Exit(2)
            slugs = list(entity)
        else:
            slugs = [a.type for a in config.entity.authorizations]
```

Add the import: `from regwatch.services.entity_types import EntityTypeService`.

Update the help text on the `--entity` Annotation:

```python
help="Slug from the EntityType table (repeatable; default: all configured authorizations)",
```

- [ ] **Step 3: Update `config.example.yaml`**

Remove the `entity_filter_ids` block (lines 78-80):

```yaml
cssf_discovery:
  base_url: "https://www.cssf.lu/en/regulatory-framework/"
  request_delay_ms: 500
  max_retries: 1
  user_agent: "RegulatoryWatcher/1.0"
  retire_min_scraped: 10
  # entity_filter_ids moved to the EntityType table (Settings → Entity Types).
  publication_types:
    ...
```

Also delete `entity_filter_ids` from `CssfDiscoveryConfig` in `regwatch/config.py` — or keep it as `dict[str, int] = Field(default_factory=dict)` with a deprecation note. To match the spec's "deprecated and ignored with warning" behavior, keep the field but log on startup if non-empty.

In `regwatch/main.py::create_app`, just before `app.state.entity_type_prompt = ...`, add:

```python
    if config.cssf_discovery.entity_filter_ids:
        logger.warning(
            "config.cssf_discovery.entity_filter_ids is deprecated; "
            "manage filter IDs from Settings → Entity Types. Ignoring %s",
            config.cssf_discovery.entity_filter_ids,
        )
```

- [ ] **Step 4: Update existing CSSF tests**

Run: `grep -rn AuthorizationType tests/`
For every match, replace with the slug string. Example: `AuthorizationType.AIFM` → `"AIFM"`. The `_make_session_factory` helper in `test_cssf_discovery_*.py` should now `seed_default_entity_types` after `Base.metadata.create_all`. Add that line to whichever helper they share.

In `tests/integration/test_cssf_discovery_service.py:29`, modify `_setup_db` so that immediately after `Base.metadata.create_all(engine)` it also calls `seed_default_entity_types`:

```python
def _setup_db(tmp_path):
    from regwatch.db.entity_type_seed import seed_default_entity_types  # noqa: PLC0415
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        seed_default_entity_types(s)
        s.commit()
    return sf
```

Apply the same one-liner to any equivalent helper in `test_cssf_discovery_finalize.py`, `test_cssf_discovery_matrix.py`, `test_cssf_retire.py`, and `test_cssf_end_to_end.py`.

For tests that don't use the FastAPI app and don't construct their own engine, use the new `seeded_entity_types` fixture: `def test_x(seeded_entity_types): sf = seeded_entity_types; ...`.

- [ ] **Step 5: Run the full suite**

Run: `pytest -x --ignore=tests/live -v`
Expected: green.

Run: `ruff check regwatch tests`
Expected: clean.

Run: `mypy regwatch`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py regwatch/cli.py regwatch/main.py config.example.yaml regwatch/config.py tests/integration/test_cli_discover_cssf.py tests/integration/test_cssf_discovery_*.py
git commit -m "feat(cli,config): CLI slug validation + deprecate entity_filter_ids YAML

CLI flag --entity now validates against the EntityType table.
config.cssf_discovery.entity_filter_ids logs a deprecation warning
on startup and is otherwise ignored — the table is canonical."
```

---

## Task 20: Manual smoke + verification

**Files:** none — verification only.

- [ ] **Step 1: Final automated checks**

Run all three in parallel:
- `pytest --ignore=tests/live`
- `ruff check regwatch tests`
- `mypy regwatch`

Expected: all green.

- [ ] **Step 2: Manual smoke**

Start uvicorn:

```bash
uvicorn regwatch.main:app --reload --host 127.0.0.1 --port 8001
```

Steps:

1. Open http://127.0.0.1:8001 — the sidebar shows a "Viewing" dropdown with "All entity types" / "AIFM" / "Chapter 15 ManCo". No hardcoded sub-links.
2. Pick "AIFM" — page reloads; cookie is set (visible in browser devtools as `active_entity_type=AIFM`). Catalog now filters to AIFM-applicable rows.
3. Navigate to Settings → Entity Types. Confirm the table shows both seeded rows.
4. Click "Add entity type". Enter:
   - Slug: `PSF_SPECIALISED`
   - Label: `PSF — Specialised`
   - CSSF filter ID: (a real Specialised-PSF filter ID from cssf.lu, or leave blank for now)
   - CSSF detail labels: `Specialised PSF, PSF spécialisé`
   - Sort order: `30`
5. Submit. Confirm the new row is in the table. Refresh the sidebar — the dropdown shows "PSF — Specialised".
6. From Catalog, click "Discover from CSSF" — verify discovery includes the new slug in its progress display.
7. Deactivate "PSF — Specialised" from Settings → Entity Types. Confirm it disappears from the sidebar dropdown but the row remains in the table under "Hidden".
8. Reactivate. Confirm it returns to the active list.

- [ ] **Step 3: Tag a checkpoint commit (no code changes — just a marker)**

```bash
git commit --allow-empty -m "chore: pluggable entity types feature complete

Manual smoke passed:
- Sidebar switcher renders from DB; cookie roundtrip works
- Settings → Entity Types CRUD works (add, deactivate, reactivate)
- New types reshape CSSF discovery, LLM prompts, and dropdowns
  without a restart"
```
