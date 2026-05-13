# Pluggable Entity Types — Design Spec

**Date:** 2026-05-13
**Status:** Approved

## Goal

Replace the hardcoded `AuthorizationType` enum (`AIFM`, `CHAPTER15_MANCO`) with a database-backed, user-editable registry of entity types. The immediate driver is adding PSF sub-types (Investment Firm, Specialised PSF, Support PSF, …) so the tool can monitor PSF-applicable CSSF circulars and regulations. The longer-term goal: a future user adds a new entity type from the Settings UI — no code change, no restart — and the new type immediately drives sidebar navigation, catalog/inbox filtering, CSSF discovery filter IDs, and LLM-classifier prompts.

A global "Viewing: All ▼" switcher at the top of the sidebar makes it one click to scope the entire app to a single entity type or back to all of them.

## Non-goals

- **Renaming the `RegulationApplicability.authorization_type` column to `entity_type`.** The column is already a `String(20)` and accepts arbitrary slugs; renaming it would touch the LLM prompts, the RAG retrieval filter, the seed YAML, every test, and the `"BOTH"` magic value. Out of scope per CLAUDE.md ("Don't propose unrelated refactoring").
- **Rewriting `RegulationApplicability` rows with `authorization_type = "BOTH"` as multiple rows.** "BOTH" stays as a CSSF/legacy shorthand handled inside `RegulationService.list`. New entity types express multi-type applicability via multiple rows, which the existing code already supports.
- **Per-PSF-sub-type seed data.** PSF sub-types are user-added via the Settings UI after upgrade — we do not auto-seed them, because the CSSF website's filter IDs and the entity's actual licenses are facts only the user knows.
- **DB-level foreign keys from `Authorization.type` / `RegulationApplicability.authorization_type` to `entity_type.slug`.** Slugs are referenced by string convention only, so a slug rename never cascades through three tables.
- **A second sidebar level showing one link per active entity type.** The current two hardcoded links (`AIFM`, `Chapter 15 ManCo`) are *replaced* by the single global switcher, not generalized into a dynamic list.

## Architecture overview

A new SQLAlchemy model `EntityType` is the single source of truth. Six call sites that today hardcode the two enum values become data-driven by reading from this table:

| Today | After |
|---|---|
| `AuthorizationType` `StrEnum` in `db/models.py:50` | Deleted. `Authorization.type` becomes `String(20)`. |
| `Literal["AIFM", "CHAPTER15_MANCO"]` in `config.py:10` | Deleted. The Pydantic `AuthorizationConfig.type` field becomes `str` and is validated against the DB on startup (warn-and-skip on miss). |
| `cssf_discovery.entity_filter_ids` dict in YAML | Moved to `entity_type.cssf_entity_filter_id`. YAML key deprecated and ignored with a startup warning. |
| `CSSF_ENTITY_LABEL_TO_AUTH` module dict in `services/cssf_discovery.py:67` | Replaced by `build_label_map(session)` reading `entity_type.cssf_detail_labels`. |
| Hardcoded `<option>` lists in `templates/catalog/list.html`, `templates/inbox/list.html`, `partials/sidebar.html` | Rendered from `EntityType` rows. |
| Hardcoded type list inside LLM prompts (`services/discovery.py:30`, `pipeline/match/classify.py:58`) | Built by `entity_type_prompt_segment(session)` at call time. |

A global "Viewing" switcher in the sidebar writes a cookie `active_entity_type=<slug>` (or empty for "All"). Every page that today reads `?authorization=…` from the query string falls back to that cookie when the query param is absent. Per-page dropdowns on Catalog and Inbox keep working — changing them updates the cookie, keeping global state in sync.

## 1. Data model

A new table `entity_type`:

```python
class EntityType(Base):
    __tablename__ = "entity_type"

    entity_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug:           Mapped[str] = mapped_column(String(40), unique=True, index=True)
    label:          Mapped[str] = mapped_column(String(120))
    cssf_entity_filter_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cssf_detail_labels:    Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    sort_order:     Mapped[int]  = mapped_column(Integer, default=100)
    active:         Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:     Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(UTC))
    updated_at:     Mapped[datetime] = mapped_column(
        TZDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
```

Column semantics:

- **`slug`** — the canonical string identifier. Stored verbatim in `Authorization.type` and `RegulationApplicability.authorization_type`. Must be valid as a URL query value (kebab-case or `SCREAMING_SNAKE_CASE`); the Settings UI validates against `^[A-Z][A-Z0-9_]{1,38}[A-Z0-9]$` to match the existing seeded slugs.
- **`label`** — human-readable display name shown in every dropdown, sidebar entry, and table heading.
- **`cssf_entity_filter_id`** — the WordPress term ID the CSSF site uses on `cssf.lu/en/regulatory-framework/?entity_type=N`. `NULL` means "this type is not crawlable from CSSF" — CSSF discovery silently skips it (with an `INFO` log).
- **`cssf_detail_labels`** — JSON list of substring patterns matched against `.entities-list li` text on CSSF detail pages, used to attribute a regulation to this type. Today the matching is case-insensitive substring; that logic stays. `NULL` means "do not match anything on detail pages" (the type still participates in listing-page crawls if `cssf_entity_filter_id` is set).
- **`sort_order`** — determines the order in the sidebar switcher and every dropdown. Two rows with the same sort order break ties on `slug` (stable for snapshot tests).
- **`active`** — soft-delete flag. Inactive rows are hidden from sidebar, dropdowns, and LLM prompts, but existing applicability rows referencing the slug are *not* deleted — switching the row back to `active=True` restores them.

**Other model changes:**

- `Authorization.type` (`models.py:110`): change from `Enum(AuthorizationType)` to `String(20)`. The unique constraint `uq_authorization_lei_type` is preserved (now constrains LEI + slug string).
- Delete the `AuthorizationType` `StrEnum` (`models.py:50-52`).
- Delete the `AuthorizationType = Literal["AIFM", "CHAPTER15_MANCO"]` alias (`config.py:10`).

`RegulationApplicability.authorization_type` is unchanged — it's already `String(20)` and already round-trips arbitrary slugs.

## 2. Config schema

`config.example.yaml` changes:

- **Removed:** the `cssf_discovery.entity_filter_ids` block. The two values move into seeded `entity_type` rows on first boot. The YAML key is left harmless if a user keeps it — startup logs a one-line warning pointing to Settings → Entity Types.
- **Kept:** `entity.authorizations[].type` (still references a slug, now validated against the DB instead of an enum). If a slug is missing on startup, log a warning and skip — don't crash; the user fixes it from the Settings page.
- **Kept:** `cssf_discovery.publication_types` (about *what kind of document* — orthogonal).

`AuthorizationConfig` (`config.py:13`) becomes:

```python
class AuthorizationConfig(BaseModel):
    type: str           # was: AuthorizationType (Literal)
    cssf_entity_id: str
```

Pydantic-level validation against the DB does not happen here (the config loads before the engine is built). Validation happens once in `regwatch/main.py::create_app` after seed runs.

## 3. UI changes

### Sidebar — global "Viewing" switcher

`templates/partials/sidebar.html` adds, above the existing `Dashboard` link:

```
Viewing
  [All entity types ▼]
```

The `<select>` is rendered from `EntityType` rows with `active=True` ordered by `sort_order, slug`. An "All entity types" option (empty value) sits at the top. Selecting an option submits to `POST /settings/active-entity-type` with `entity_type=<slug>` (or empty). The route sets / clears the `active_entity_type` cookie (httponly, samesite=lax, max-age 30 days) and returns 303 to the `Referer` (falling back to `/`).

The two hardcoded sidebar `<a href="/catalog?authorization=…">` lines (lines 24-25) are deleted.

### Catalog and Inbox dropdowns

`templates/catalog/list.html` and `templates/inbox/list.html` `<select>` options become a `{% for et in entity_types %}` loop. The render context already provides `entity_types` via the new context processor described below. Selecting an option submits the form (existing behavior) AND the corresponding GET route also writes the cookie before rendering — global state stays in sync without JavaScript.

Today's filter precedence (matches the existing `/catalog` cookie behavior in `web/routes/catalog.py:62-75`):
- Any query string present (e.g. `?authorization=X`, `?authorization=`, or `?search=foo`) → URL wins; an empty `authorization=` is treated as "All".
- Bare `/catalog` with no query string + cookie → 303 redirect to the cookie's last filter (existing behavior, unchanged).
- Bare `/catalog` with no query string + no cookie → "All" (no filter).

### Render context — `web/templates_context.py`

`render_page` already injects `sidebar_badges`. Two additions:

- `entity_types: list[EntityTypeDTO]` — active rows ordered by `sort_order`.
- `active_entity_type: str` — the cookie value (empty string for "All").

Both are read once per request from a new `EntityTypeService.list_active()` call. The service caches nothing — entity-type writes are rare and the table is tiny (<50 rows expected).

### Settings → Entity Types

New page at `/settings/entity-types`. Linked from the sidebar Settings group, beneath "Schedules":

```
Settings
  Extraction Fields
  Schedules
  Entity Types        <-- new
```

The page renders a table:

| Slug | Label | CSSF filter ID | Sort | Active | Actions |

Below the table: a "+ Add entity type" button toggles an inline HTMX form with fields:

- Slug — text input, validated against the slug regex above and uniqueness.
- Label — text input.
- CSSF filter ID — integer input, optional.
- CSSF detail labels — text input, comma-separated, optional.
- Sort order — integer, default 100.
- Active — checkbox, default checked.

Submitting POSTs to `/settings/entity-types`. Editing an existing row is an HTMX in-place form. "Deactivate" toggles `active=False` and moves the row to a "Hidden" section at the bottom. "Reactivate" moves it back.

No row is ever hard-deleted from the UI — soft-delete only — because hard-deleting a row with referencing `RegulationApplicability` slugs would leave orphan strings nobody can resurrect.

## 4. CSSF discovery integration

Two changes inside `regwatch/services/cssf_discovery.py`:

### Filter-ID lookup reads the DB

`CssfDiscoveryService.run()` today does:

```python
entity_filter_id = self._config.entity_filter_ids.get(et.value)
```

This becomes a session-scoped lookup loaded once at the top of `run()`:

```python
with self._sf() as s:
    by_slug = {
        et.slug: et
        for et in s.scalars(select(EntityType).where(EntityType.active.is_(True))).all()
    }
```

Per-slug iteration uses `by_slug[slug].cssf_entity_filter_id`. Slugs with `None` filter_id are skipped with `logger.info("skipping %s: no CSSF filter ID configured", slug)`.

The `entity_types` parameter on `CssfDiscoveryService.run()` changes from `list[AuthorizationType]` to `list[str]` (slugs). Callers (`web/routes/catalog.py::catalog_discover_cssf` and `regwatch/cli.py`) already construct enum values from string user input; they switch to passing the strings directly with a validity check against the DB.

### Label map becomes a function

The module-level `CSSF_ENTITY_LABEL_TO_AUTH` dict (`cssf_discovery.py:67`) goes away. Replaced by:

```python
def build_label_map(session: Session) -> dict[str, str]:
    """Substring pattern -> slug, built from EntityType.cssf_detail_labels."""
    out: dict[str, str] = {}
    for et in session.scalars(
        select(EntityType).where(EntityType.active.is_(True))
    ).all():
        for label in (et.cssf_detail_labels or []):
            out[label] = et.slug
    return out
```

`_map_labels_to_auth_types` (`cssf_discovery.py:78`) is renamed to `_map_labels_to_slugs` and now returns `list[str]`. Its substring-match logic is unchanged. Downstream callers already pass the result into `RegulationApplicability.authorization_type` (a `String(20)`), so the return-type change is contained.

`CssfDiscoveryService._ensure_applicability` already takes the slug as a `String(20)` argument — no change.

## 5. LLM prompts

Two prompts hardcode the entity-type list today:

- `services/discovery.py:30` — `_CLASSIFY_SYSTEM` lists `"AIFM", "CHAPTER15_MANCO", "CREDIT_INSTITUTION", "CASP", "INVESTMENT_FIRM", "INSURANCE", "PENSION_FUND", or "ALL"`.
- `pipeline/match/classify.py:58-59` — `classify_entity_types()` system prompt lists the same set with prose explanations.

Both are converted from module-level constants into builders that take a session:

```python
# regwatch/services/entity_types.py
def prompt_segment(session: Session) -> str:
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

`services/discovery.py::DiscoveryService._classify_regulation` already has a session in scope, so it calls `prompt_segment(self._session)` and `.format()`s the result into the system message at call time.

`pipeline/match/classify.py::classify_entity_types` does **not** have a session in scope — it's invoked from the persist phase via `CombinedMatcher`. Two options:

1. **Plumb the session through** as a function argument (mechanical, touches `combined.py` and the pipeline factory).
2. **Cache the prompt string on `app.state.entity_type_prompt`** and refresh it after every entity-type CRUD write (a single line in the Settings route handler).

Implementation decision deferred to the writing-plans skill, but the preferred path is **(2)** — the prompt cache is read-only at call time, refreshes are rare, and we avoid threading a session through three layers. The CLI's one-shot pipeline run rebuilds the cache before the matcher kicks off (same one-liner in `cli.py::run-pipeline`).

## 6. Migration / first-boot

The codebase uses `Base.metadata.create_all` + small additive migrations in `regwatch/db/migrations.py` (no Alembic). This change follows that pattern.

**On the next `regwatch init-db` or app startup:**

1. `Base.metadata.create_all` creates the `entity_type` table.
2. `regwatch/db/migrations.py` gets a new `migrate_authorization_type_to_string(engine)` step that rewrites the `authorization.type` column from `VARCHAR(...)` (the enum's underlying type) to plain `VARCHAR(20)`. SQLite doesn't enforce column type strictly, so the migration is best-effort: it runs an `ALTER TABLE` only if needed and logs the result.
3. A new `regwatch/db/entity_type_seed.py` (parallel to `extraction_field_seed.py`) inserts the two existing values **only if the table is empty**:

| slug | label | cssf_entity_filter_id | cssf_detail_labels | sort_order |
|---|---|---:|---|---:|
| `AIFM` | AIFM | 502 | `["Alternative investment fund manager", "AIFM"]` | 10 |
| `CHAPTER15_MANCO` | Chapter 15 ManCo | 2001 | `["UCITS management company", "UCITS management companies", "Chapter 15 management company", "Chapter 15 management companies", "Management company"]` | 20 |

4. Existing `Authorization`, `RegulationApplicability`, and `Regulation.applicable_entity_types` rows are **untouched** — their slug strings already match the seeded `entity_type.slug` values.

5. PSF sub-types are **NOT** auto-seeded. The user adds them through `/settings/entity-types` after upgrade. Once they fill in CSSF filter ID(s), the next discovery run picks them up.

**Rollback story:** dropping the `entity_type` table doesn't break the rest of the app — every consumer code path treats slugs as strings. The sidebar switcher renders empty, the catalog filter shows only "All", and CSSF discovery raises a clear `RuntimeError`. If the migration goes wrong, the user can drop the table and restart without data loss.

**Upgrade ordering note:** if any user has manually added `CASP`, `CREDIT_INSTITUTION`, etc. into `RegulationApplicability` rows (the LLM classifier already produces these), those orphan slugs will silently be filtered out of UI dropdowns until the user adds matching `entity_type` rows. The seeder logs a one-line summary at startup of "orphan slugs in regulation_applicability not present in entity_type:" so this is visible.

## 7. Testing

Following existing conventions (CLAUDE.md): fresh SQLite in `tmp_path`, no DB mocking, mock only Ollama and outbound HTTP.

### New unit tests (`tests/unit/`)

- **`test_entity_type_model.py`** — schema, unique-slug constraint, defaults (`active=True`, `sort_order=100`), JSON round-trip of `cssf_detail_labels`.
- **`test_entity_type_service.py`** — CRUD: create / update / soft-delete, slug-uniqueness, "deactivate keeps existing applicability rows" (regression guard), `list_active()` ordering.
- **`test_entity_type_prompt_segment.py`** — `prompt_segment()` excludes inactive rows, orders by `sort_order`, includes the `"ALL"` sentinel.
- **`test_entity_type_seed.py`** — idempotent on non-empty table; populates the two known rows on empty table; matches the existing `CSSF_ENTITY_LABEL_TO_AUTH` substring set exactly.

### New integration tests (`tests/integration/`)

- **`test_entity_type_routes.py`** — Settings → Entity Types CRUD: GET list, POST add (success + slug regex failure + duplicate slug failure), POST edit, POST deactivate, POST reactivate. HTMX fragment responses.
- **`test_active_entity_type_cookie.py`** — sidebar switcher: POST `/settings/active-entity-type` sets cookie; subsequent Catalog/Inbox GET filters accordingly; "All" clears the cookie; query param wins over cookie.

### Modified tests (~10 files)

- **`tests/unit/test_db_models.py`** — remove `AuthorizationType` import; add `EntityType` model assertion.
- **`tests/integration/test_cssf_discovery_*.py`** (5 files: `*_finalize`, `*_service`, `*_matrix`, `*_retire`, `*_end_to_end`) — each gains a fixture that seeds two `entity_type` rows. Shared in `tests/conftest.py` as `seeded_entity_types`.
- **`tests/unit/test_cssf_scraper.py`** — unaffected (pure HTTP).
- **`tests/integration/test_app_smoke.py`** — assert the new sidebar dropdown renders without 500.
- **`tests/integration/test_cli_discover_cssf.py`** — `--entity-type AIFM` works against DB-backed lookup.
- **`tests/unit/test_rules_matcher.py`** — verify no enum import lingers.

### Test data

- `tests/fixtures/cssf/` HTML fixtures unchanged (selectors haven't moved).
- New `tests/conftest.py::seeded_entity_types` fixture inserts the two default rows.

### Verification before "done"

- `pytest` passes (today ~467; we'll add ~20-30).
- `ruff check regwatch` clean.
- `mypy regwatch` clean (the main reason approach A beats a dynamic-enum approach — types stay statically checkable).
- Manual smoke: launch uvicorn, visit `/settings/entity-types`, add a `PSF_SPECIALISED` row with a CSSF filter ID, run a CSSF discovery from the Catalog page, verify a PSF reg appears in the catalog under "Viewing: PSF Specialised".
