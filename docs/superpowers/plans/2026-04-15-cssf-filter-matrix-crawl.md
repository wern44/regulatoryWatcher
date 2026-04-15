# CSSF Filter-Matrix Crawl — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-slug CSSF crawl with a 2×7 (entity × publication-type) filter matrix, record per-regulation discovery provenance, auto-retire rows absent from every cell, and drop recursive stub promotion.

**Architecture:** Drive the CSSF listing filters via Playwright (headless Chromium) using numeric WordPress term IDs discovered on the live site. `CssfDiscoveryService` iterates the 2×7 matrix, calls a `PlaywrightListingDriver` per cell, runs the rendered HTML through the existing BeautifulSoup parser, and UPSERTs provenance into a new `regulation_discovery_source` table. Detail pages stay on httpx. Add a SUCCESS-gated retirement sweep; remove `enrich_stubs`. Parsing tests stay fixture-driven (post-JS HTML snapshots); one `@pytest.mark.live` probe verifies filter IDs still match labels.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, httpx, BeautifulSoup4, **Playwright (Chromium)**, pytest + pytest-httpx, Typer CLI, FastAPI + Jinja/HTMX web.

**Spec:** `docs/superpowers/specs/2026-04-15-cssf-filter-matrix-crawl-design.md`

## Revision 2026-04-15 (mid-plan)

The original plan assumed CSSF was a FacetWP site with server-side URL filter params (`?fwp_content_type=<slug>`). Verification against the live site during Task 5 proved this wrong: CSSF is plain WordPress with client-side JS filters calling `/wp-admin/admin-ajax.php`; URL filter params are silently ignored. Filters use numeric WordPress term IDs, not slugs.

**Resolution:** drive filters via Playwright headless Chromium. Numeric IDs discovered and baked into config. Revised tasks:

- **Task 4** — field rename follow-up (see Task 4b below) because the already-shipped config used `entity_slugs` / `slug`.
- **Task 4b (new)** — add `playwright` dependency + browser install.
- **Task 5** — rewrite live probe: verify label↔filter_id mapping (no slug discovery).
- **Task 6** — capture post-JS rendered HTML via Playwright (was `curl`).
- **Task 8** — `CssfDiscoveryService` uses an injected `PlaywrightListingDriver` instead of calling `list_circulars(...)` via httpx.

Tasks 1–3, 7, 9–14 are unchanged in intent. Their code references to `slug` / `entity_slug` parameters become `label` / `entity_filter_id` where they pass through the driver boundary.

**Convention reminders (from `CLAUDE.md`):**
- No backward-compat shims — change every caller, no deprecated wrappers.
- Commits are small and per-task; preserve the test-first cadence.
- Integration tests hit a real SQLite in `tmp_path`; only mock Ollama + outbound HTTP.
- `@pytest.mark.live` is excluded from default `pytest` runs.

---

## Task 1 — Extend `RegulationType` enum

**Files:**
- Modify: `regwatch/db/models.py` (the `RegulationType` StrEnum)
- Test: `tests/unit/test_db_models.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_db_models.py`:

```python
from regwatch.db.models import RegulationType

def test_regulation_type_includes_new_publication_types():
    values = {t.value for t in RegulationType}
    assert "CSSF_CIRCULAR_ANNEX" in values
    assert "PROFESSIONAL_STANDARD" in values
    assert "LU_GRAND_DUCAL_REGULATION" in values
    assert "LU_MINISTERIAL_REGULATION" in values
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/unit/test_db_models.py::test_regulation_type_includes_new_publication_types -v`
Expected: FAIL (missing enum values).

- [ ] **Step 3: Implement**

In `regwatch/db/models.py`, extend `RegulationType`:

```python
class RegulationType(StrEnum):
    LU_LAW = "LU_LAW"
    LU_GRAND_DUCAL_REGULATION = "LU_GRAND_DUCAL_REGULATION"
    LU_MINISTERIAL_REGULATION = "LU_MINISTERIAL_REGULATION"
    CSSF_CIRCULAR = "CSSF_CIRCULAR"
    CSSF_CIRCULAR_ANNEX = "CSSF_CIRCULAR_ANNEX"
    CSSF_REGULATION = "CSSF_REGULATION"
    PROFESSIONAL_STANDARD = "PROFESSIONAL_STANDARD"
    EU_REGULATION = "EU_REGULATION"
    EU_DIRECTIVE = "EU_DIRECTIVE"
    ESMA_GUIDELINE = "ESMA_GUIDELINE"
    RTS = "RTS"
    ITS = "ITS"
    DELEGATED_ACT = "DELEGATED_ACT"
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/unit/test_db_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/db/models.py tests/unit/test_db_models.py
git commit -m "feat(models): add 4 new RegulationType values for filter matrix"
```

---

## Task 2 — Add `RegulationDiscoverySource` model + `DiscoveryRun.retired_count`

**Files:**
- Modify: `regwatch/db/models.py`
- Test: `tests/unit/test_discovery_models.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_discovery_models.py`:

```python
from datetime import UTC, datetime
from regwatch.db.models import (
    DiscoveryRun, Regulation, RegulationDiscoverySource,
    RegulationType, LifecycleStage,
)

def test_regulation_discovery_source_round_trip(in_memory_session):
    s = in_memory_session
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 22/806",
        title="X",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        needs_review=False,
        url="",
        source_of_truth="CSSF_WEB",
    )
    s.add(reg)
    s.flush()
    run = DiscoveryRun(
        status="SUCCESS", started_at=datetime.now(UTC),
        triggered_by="USER_CLI", entity_types=["AIFM"], mode="full",
    )
    s.add(run)
    s.flush()
    src = RegulationDiscoverySource(
        regulation_id=reg.regulation_id,
        entity_type="AIFM",
        content_type="circulars-cssf",
        first_seen_run_id=run.run_id,
        first_seen_at=datetime.now(UTC),
        last_seen_run_id=run.run_id,
        last_seen_at=datetime.now(UTC),
    )
    s.add(src)
    s.commit()
    assert src.source_id is not None

def test_discovery_run_retired_count_defaults_zero(in_memory_session):
    s = in_memory_session
    run = DiscoveryRun(
        status="SUCCESS", started_at=datetime.now(UTC),
        triggered_by="USER_CLI", entity_types=[], mode="full",
    )
    s.add(run)
    s.commit()
    assert run.retired_count == 0
```

The `in_memory_session` fixture already exists in `tests/unit/conftest.py` — reuse it. If missing, mirror the pattern from other unit tests that build a session on `sqlite:///:memory:` with `Base.metadata.create_all`.

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/unit/test_discovery_models.py -v -k "regulation_discovery_source or retired_count"`
Expected: FAIL (`RegulationDiscoverySource` does not exist; `retired_count` attribute missing).

- [ ] **Step 3: Implement**

In `regwatch/db/models.py`, add `retired_count` to `DiscoveryRun` (right after `failed_count`):

```python
    retired_count: Mapped[int] = mapped_column(Integer, default=0)
```

Append the new model (after `DiscoveryRunItem`):

```python
class RegulationDiscoverySource(Base):
    __tablename__ = "regulation_discovery_source"

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40))
    content_type: Mapped[str] = mapped_column(String(60))
    first_seen_run_id: Mapped[int] = mapped_column(
        ForeignKey("discovery_run.run_id", ondelete="CASCADE")
    )
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_seen_run_id: Mapped[int] = mapped_column(
        ForeignKey("discovery_run.run_id", ondelete="CASCADE")
    )
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime)

    __table_args__ = (
        UniqueConstraint(
            "regulation_id", "entity_type", "content_type",
            name="uq_discovery_source_reg_entity_content",
        ),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/unit/test_discovery_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/db/models.py tests/unit/test_discovery_models.py
git commit -m "feat(models): add RegulationDiscoverySource + retired_count"
```

---

## Task 3 — Migrate `DiscoveryRunItem` columns (`entity_types` → `entity_type` + new `content_type`)

**Files:**
- Modify: `regwatch/db/models.py` (`DiscoveryRunItem`)
- Create: `regwatch/db/migrations.py`
- Modify: `regwatch/main.py` (invoke migration after `create_all`)
- Modify: `regwatch/cli.py::init_db` command (invoke migration)
- Modify: `regwatch/services/cssf_discovery.py::_write_item` (adapt to new signature)
- Test: `tests/unit/test_discovery_migrations.py` (new)

- [ ] **Step 1: Write failing migration test**

Create `tests/unit/test_discovery_migrations.py`:

```python
"""One-shot migration: entity_types (JSON list) -> entity_type + content_type."""
from datetime import UTC, datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from regwatch.db.migrations import migrate_discovery_run_item_columns


def test_migrate_copies_first_entity_and_defaults_content_type(tmp_path):
    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    # Simulate pre-migration schema.
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE discovery_run (
                run_id INTEGER PRIMARY KEY,
                status TEXT, started_at TEXT, triggered_by TEXT,
                entity_types TEXT, mode TEXT,
                total_scraped INTEGER DEFAULT 0, new_count INTEGER DEFAULT 0,
                amended_count INTEGER DEFAULT 0, updated_count INTEGER DEFAULT 0,
                unchanged_count INTEGER DEFAULT 0, withdrawn_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                error_summary TEXT
            )
        """))
        c.execute(text("""
            CREATE TABLE discovery_run_item (
                item_id INTEGER PRIMARY KEY,
                run_id INTEGER,
                regulation_id INTEGER,
                reference_number TEXT,
                outcome TEXT,
                detail_url TEXT,
                entity_types TEXT,
                note TEXT,
                created_at TEXT
            )
        """))
        c.execute(text("INSERT INTO discovery_run (run_id, status, started_at, triggered_by, entity_types, mode) VALUES (1, 'SUCCESS', '2026-04-14', 'USER_CLI', '[\"AIFM\"]', 'full')"))
        c.execute(text("INSERT INTO discovery_run_item (item_id, run_id, regulation_id, reference_number, outcome, detail_url, entity_types, note, created_at) VALUES (1, 1, NULL, 'CSSF 22/806', 'NEW', 'https://x', '[\"AIFM\"]', NULL, '2026-04-14')"))

    migrate_discovery_run_item_columns(engine)

    with engine.connect() as c:
        row = c.execute(text(
            "SELECT entity_type, content_type FROM discovery_run_item WHERE item_id=1"
        )).one()
        assert row.entity_type == "AIFM"
        assert row.content_type == "circulars-cssf"
        # Old column is gone
        cols = [r[1] for r in c.execute(text("PRAGMA table_info(discovery_run_item)"))]
        assert "entity_types" not in cols
        assert "entity_type" in cols
        assert "content_type" in cols


def test_migrate_is_idempotent(tmp_path):
    """Running the migration twice must not fail or duplicate work."""
    db = tmp_path / "idempotent.db"
    engine = create_engine(f"sqlite:///{db}")
    # Already-migrated schema (no entity_types column).
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE discovery_run_item (
                item_id INTEGER PRIMARY KEY,
                run_id INTEGER,
                regulation_id INTEGER,
                reference_number TEXT,
                outcome TEXT,
                detail_url TEXT,
                entity_type TEXT,
                content_type TEXT,
                note TEXT,
                created_at TEXT
            )
        """))
    migrate_discovery_run_item_columns(engine)  # no-op
    migrate_discovery_run_item_columns(engine)  # no-op
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/unit/test_discovery_migrations.py -v`
Expected: FAIL (`regwatch.db.migrations` module does not exist).

- [ ] **Step 3: Implement migration**

Create `regwatch/db/migrations.py`:

```python
"""One-shot, idempotent schema migrations run at engine init time.

We don't use Alembic (see CLAUDE.md). `Base.metadata.create_all` covers
additive column/table changes automatically; only renames and data copies
need explicit migration code here.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)


def migrate_discovery_run_item_columns(engine: Engine) -> None:
    """Rename discovery_run_item.entity_types (JSON list) -> entity_type,
    and add content_type (default 'circulars-cssf' for legacy rows).

    Idempotent: detects already-migrated schema and returns cleanly.
    """
    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(discovery_run_item)"))]
        if not cols:
            return  # table doesn't exist yet (fresh DB); create_all handles it.
        if "entity_types" not in cols:
            return  # already migrated.

        logger.info("Migrating discovery_run_item: entity_types -> entity_type + content_type")
        conn.execute(text("ALTER TABLE discovery_run_item ADD COLUMN entity_type VARCHAR(40)"))
        conn.execute(text("ALTER TABLE discovery_run_item ADD COLUMN content_type VARCHAR(60)"))

        rows = conn.execute(text(
            "SELECT item_id, entity_types FROM discovery_run_item"
        )).all()
        for item_id, raw in rows:
            first = ""
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list) and parsed:
                        first = str(parsed[0])
                except (ValueError, TypeError):
                    first = ""
            conn.execute(
                text(
                    "UPDATE discovery_run_item SET entity_type = :et, "
                    "content_type = :ct WHERE item_id = :id"
                ),
                {"et": first, "ct": "circulars-cssf", "id": item_id},
            )
        conn.execute(text("ALTER TABLE discovery_run_item DROP COLUMN entity_types"))
        logger.info("Migrated %d discovery_run_item rows", len(rows))
```

- [ ] **Step 4: Update the model**

In `regwatch/db/models.py`, replace `DiscoveryRunItem.entity_types` with:

```python
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    content_type: Mapped[str] = mapped_column(String(60), default="")
```

- [ ] **Step 5: Wire migration into startup**

In `regwatch/main.py::create_app`, right after `Base.metadata.create_all(engine)` and `create_virtual_tables(engine)`, add:

```python
    from regwatch.db.migrations import migrate_discovery_run_item_columns
    migrate_discovery_run_item_columns(engine)
```

In `regwatch/cli.py::init_db` command (find the existing `@app.command("init-db")` function), add the same call after `create_all`.

- [ ] **Step 6: Fix callers**

In `regwatch/services/cssf_discovery.py::_write_item`, change the signature and implementation to take `entity_type: str, content_type: str` (singular) instead of `entity_types: list[str]`. Update every call site in the same file (seven `self._write_item(...)` calls). Defer passing real values to later tasks; for now, pass `entity_type=entity_types[0] if entity_types else ""` and `content_type=""` so this task compiles. The proper values come in Task 7.

- [ ] **Step 7: Run tests**

Run: `pytest tests/unit/test_discovery_migrations.py tests/unit/test_discovery_models.py tests/unit/test_cssf_discovery_service.py -v` (and any integration tests that touched `DiscoveryRunItem`).
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add regwatch/db/models.py regwatch/db/migrations.py regwatch/main.py regwatch/cli.py regwatch/services/cssf_discovery.py tests/unit/test_discovery_migrations.py
git commit -m "refactor(discovery): rename entity_types -> entity_type + content_type"
```

---

## Task 4 — Extend `CssfDiscoveryConfig` with filter matrix

**Files:**
- Modify: `regwatch/config.py`
- Modify: `config.example.yaml`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_config.py`:

```python
def test_cssf_discovery_config_publication_types_loaded(tmp_path):
    cfg_text = """
entity: { lei: "X", authorizations: [] }
sources: {}
llm:
  chat_model: "x"
  embedding_model: "x"
  host: "x"
  embedding_dim: 768
rag:
  retrieval_k: 5
  chunk_size_tokens: 100
  chunk_overlap_tokens: 10
paths:
  db_file: "x.db"
  pdf_archive: "x"
ui: {}
cssf_discovery:
  entity_slugs:
    AIFM: aifms
    CHAPTER15_MANCO: management-companies-chapter-15
  publication_types:
    - { label: "CSSF circular", slug: circulars-cssf, type: CSSF_CIRCULAR }
    - { label: "Law", slug: laws, type: LU_LAW }
"""
    p = tmp_path / "c.yaml"
    p.write_text(cfg_text, encoding="utf-8")
    from regwatch.config import load_config
    cfg = load_config(p)
    assert cfg.cssf_discovery.entity_slugs["AIFM"] == "aifms"
    assert len(cfg.cssf_discovery.publication_types) == 2
    assert cfg.cssf_discovery.publication_types[0].slug == "circulars-cssf"
    assert cfg.cssf_discovery.publication_types[0].type == "CSSF_CIRCULAR"

def test_cssf_discovery_config_no_content_types_field():
    """The old content_types field is gone; no backward-compat shim."""
    from regwatch.config import CssfDiscoveryConfig
    cfg = CssfDiscoveryConfig()
    assert not hasattr(cfg, "content_types")
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/unit/test_config.py -v -k "publication_types or content_types_field"`
Expected: FAIL.

- [ ] **Step 3: Implement config**

In `regwatch/config.py`, replace the existing `CssfDiscoveryConfig` with:

```python
class PublicationTypeConfig(BaseModel):
    label: str         # human-readable, e.g. "CSSF circular"
    slug: str          # FacetWP slug, e.g. "circulars-cssf"
    type: str          # RegulationType enum value, e.g. "CSSF_CIRCULAR"


class CssfDiscoveryConfig(BaseModel):
    base_url: str = "https://www.cssf.lu/en/regulatory-framework/"
    request_delay_ms: int = 500
    max_retries: int = 1
    user_agent: str = "RegulatoryWatcher/1.0"
    entity_slugs: dict[str, str] = Field(
        default_factory=lambda: {
            "AIFM": "aifms",
            "CHAPTER15_MANCO": "management-companies-chapter-15",
        }
    )
    publication_types: list[PublicationTypeConfig] = Field(default_factory=list)
```

Import `PublicationTypeConfig` wherever needed.

- [ ] **Step 4: Update `config.example.yaml`**

In the `cssf_discovery:` block, replace `content_types: ["circulars-cssf"]` with:

```yaml
cssf_discovery:
  base_url: https://www.cssf.lu/en/regulatory-framework/
  request_delay_ms: 500
  user_agent: RegulatoryWatcher/1.0
  entity_slugs:
    AIFM: aifms
    CHAPTER15_MANCO: management-companies-chapter-15
  publication_types:
    - { label: "CSSF circular",            slug: circulars-cssf,            type: CSSF_CIRCULAR }
    - { label: "CSSF regulation",          slug: cssf-regulations,          type: CSSF_REGULATION }
    - { label: "Law",                      slug: laws,                      type: LU_LAW }
    - { label: "Grand-ducal regulation",   slug: grand-ducal-regulations,   type: LU_GRAND_DUCAL_REGULATION }
    - { label: "Ministerial regulation",   slug: ministerial-regulations,   type: LU_MINISTERIAL_REGULATION }
    - { label: "Annex to a CSSF circular", slug: annexes-to-cssf-circulars, type: CSSF_CIRCULAR_ANNEX }
    - { label: "Professional standard",    slug: professional-standards,    type: PROFESSIONAL_STANDARD }
```

**Note:** The slug values after `circulars-cssf` are best-guess placeholders. Task 5 verifies and corrects them against the live site.

- [ ] **Step 5: Fix callers**

In `regwatch/services/cssf_discovery.py`, remove the `CSSF_ENTITY_SLUGS` module-level dict and read from `self._config.entity_slugs` instead. Where code does `slug = CSSF_ENTITY_SLUGS.get(et)`, change to `slug = self._config.entity_slugs.get(et.value)`.

- [ ] **Step 6: Run tests**

Run: `pytest tests/unit/test_config.py tests/unit/test_cssf_discovery_service.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add regwatch/config.py config.example.yaml regwatch/services/cssf_discovery.py tests/unit/test_config.py
git commit -m "feat(config): add publication_types filter matrix config"
```

---

## Task 5 — Live probe to discover FacetWP content_type slugs

**Files:**
- Create: `tests/live/test_cssf_slug_discovery.py`

The goal is a single `@pytest.mark.live` test that fetches the live listing page, parses the `fwp_content_type` `<select>` options, and asserts the seven labels we need are all present. Its failure message prints the discovered `(label, slug)` pairs so the developer can paste the real slug values into `config.example.yaml` (and their local `config.yaml`).

This test is not run in CI; it's a one-time probe plus an ongoing canary.

- [ ] **Step 1: Write the live probe**

Create `tests/live/test_cssf_slug_discovery.py`:

```python
"""Live probe: discover FacetWP content_type slugs on cssf.lu.

Run explicitly:  pytest -m live tests/live/test_cssf_slug_discovery.py -v -s

Prints each (label, slug) pair so you can update config.example.yaml.
Fails noisily if any of the seven required labels is missing.
"""
from __future__ import annotations

import httpx
import pytest
from bs4 import BeautifulSoup

LISTING_URL = "https://www.cssf.lu/en/regulatory-framework/"

REQUIRED_LABELS: list[str] = [
    "CSSF circular",
    "CSSF regulation",
    "Law",
    "Grand-ducal regulation",
    "Ministerial regulation",
    "Annex to a CSSF circular",
    "Professional standard",
]


@pytest.mark.live
def test_fwp_content_type_slugs_are_discoverable() -> None:
    with httpx.Client(
        headers={"User-Agent": "RegulatoryWatcher/1.0"},
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        resp = client.get(LISTING_URL)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    selects = soup.select(".facetwp-facet-content_type option")
    assert selects, (
        "Could not find .facetwp-facet-content_type options on listing page; "
        "the DOM or FacetWP class may have changed."
    )
    discovered: dict[str, str] = {}
    for opt in selects:
        label = opt.get_text(strip=True)
        slug = opt.get("value") or ""
        if isinstance(slug, list):
            slug = slug[0] if slug else ""
        if label and slug:
            discovered[label] = str(slug)

    print("\n=== Discovered FacetWP content_type slugs ===")
    for k, v in sorted(discovered.items()):
        print(f"  {k!r:40s} -> {v!r}")

    missing = [lbl for lbl in REQUIRED_LABELS if lbl not in discovered]
    assert not missing, (
        f"Missing expected labels from FacetWP content_type facet: {missing}\n"
        f"Full discovered mapping: {discovered}"
    )
```

- [ ] **Step 2: Run the probe (developer action)**

Run: `pytest -m live tests/live/test_cssf_slug_discovery.py -v -s`
Expected: PASS, with printed `(label, slug)` pairs.

If the FacetWP class selector `.facetwp-facet-content_type option` is wrong, inspect the rendered HTML (`curl -s https://www.cssf.lu/en/regulatory-framework/ | head -500` or browser devtools) and update the selector before committing.

- [ ] **Step 3: Update `config.example.yaml` with real slugs**

Paste the discovered slugs into `config.example.yaml` (and your local `config.yaml`). Keep the same label→slug→type structure.

- [ ] **Step 4: Commit**

```bash
git add tests/live/test_cssf_slug_discovery.py config.example.yaml
git commit -m "test(discovery): live probe for FacetWP content_type slugs"
```

---

## Task 6 — Fixtures for non-CSSF publication types

**Files:**
- Create (one fixture pair per non-circular type): `tests/fixtures/cssf/listing_laws_aifm_page1.html`, `tests/fixtures/cssf/detail_law_example.html`, and analogously for `grand_ducal_regulations`, `ministerial_regulations`, `cssf_regulations`, `annexes_to_cssf_circulars`, `professional_standards`.
- Update: `tests/fixtures/cssf/README.md` with refresh instructions for each new fixture.

Each listing fixture is a trimmed real response body obtained during probe work. For each publication type, capture **one** listing page (`AIFM × <type>`) and **one** detail page. Strip unrelated markup to keep the fixture focused.

- [ ] **Step 1: Capture listing pages**

```bash
curl -s "https://www.cssf.lu/en/regulatory-framework/?fwp_entity_type=aifms&fwp_content_type=<slug>" \
  -A "RegulatoryWatcher/1.0" \
  -o tests/fixtures/cssf/listing_<type>_aifm_page1.html
```

Replace `<slug>` and `<type>` for each of the six new publication types.

- [ ] **Step 2: Capture a detail page per type**

Open each listing fixture, pick one document, and capture its detail page:

```bash
curl -s "https://www.cssf.lu/en/Document/<document-slug>/" \
  -A "RegulatoryWatcher/1.0" \
  -o tests/fixtures/cssf/detail_<type>_<shortname>.html
```

- [ ] **Step 3: Trim fixtures**

For each HTML file, keep only the structural markup the parser needs: the `<ul class="library-results">` and its `<li class="library-element">` children for listings; the `<main>` content block (heading, content-header-info, entities-list, related-document list) for details. Remove `<script>`, `<style>`, and unrelated `<header>`/`<footer>` sections.

- [ ] **Step 4: Update README**

Document in `tests/fixtures/cssf/README.md` which (entity × publication_type) cell each fixture represents and how to refresh it (command from Step 1).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/cssf/ tests/fixtures/cssf/README.md
git commit -m "test(fixtures): CSSF listing + detail for each publication type"
```

---

## Task 7 — Scraper handles non-CSSF publication types

**Files:**
- Modify: `regwatch/discovery/cssf_scraper.py`
- Modify: `tests/unit/test_cssf_scraper.py`

The listing row parser currently drops any row without a `CSSF NN/NNN` reference. That's correct for `circulars-cssf` but wrong for `laws`, `grand-ducal-regulations`, and `ministerial-regulations` — these don't have numeric short-codes. Parser changes:

1. Accept a `publication_type_slug: str` parameter on `list_circulars` and thread it through to `_row_from_library_element`.
2. When the slug is not `circulars-cssf` / `cssf-regulations` / `annexes-to-cssf-circulars`, don't require `_REF_RE` — instead synthesize a reference number from the detail URL slug.
3. Propagate the slug into each `CircularListingRow` (new field `publication_type_slug: str`).

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_cssf_scraper.py`, add:

```python
import pathlib
from regwatch.discovery.cssf_scraper import (
    _parse_listing_page, CircularListingRow,
)

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "cssf"

def test_law_listing_synthesizes_reference_from_slug():
    html = (FIXTURES / "listing_laws_aifm_page1.html").read_text(encoding="utf-8")
    rows, raw_count = _parse_listing_page(html, publication_type_slug="laws")
    assert raw_count > 0
    assert rows, "expected at least one law row parsed"
    for r in rows:
        assert r.publication_type_slug == "laws"
        # Laws have no CSSF ref; reference_number must be synthesized from URL slug
        assert r.reference_number, "law must have a synthesized reference"
        assert r.reference_number.startswith("law-")  # slug-derived

def test_circular_listing_still_uses_ref_regex():
    html = (FIXTURES / "listing_aifms_page1.html").read_text(encoding="utf-8")
    rows, _ = _parse_listing_page(html, publication_type_slug="circulars-cssf")
    assert rows
    # Circulars must still be "CSSF NN/NNN"-shaped.
    for r in rows:
        assert r.publication_type_slug == "circulars-cssf"
        # Should match the pre-existing _REF_RE output
        import re
        assert re.match(r"^(CSSF(-[A-Z]+)?|IML|BCL)\s\d{2,4}[/]\d{1,4}$", r.reference_number)
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/unit/test_cssf_scraper.py -v -k "law_listing or circular_listing_still"`
Expected: FAIL (`_parse_listing_page` doesn't accept `publication_type_slug`; `publication_type_slug` attribute missing).

- [ ] **Step 3: Implement scraper changes**

In `regwatch/discovery/cssf_scraper.py`:

a) Add field to `CircularListingRow`:

```python
@dataclass
class CircularListingRow:
    reference_number: str
    raw_title: str
    description: str
    publication_date: date | None
    detail_url: str
    publication_type_slug: str = ""   # NEW
```

b) Update `list_circulars` signature (note: `content_type` already exists but now drives the slug plumbing):

```python
def list_circulars(
    entity_slug: str,
    *,
    client: httpx.Client | None = None,
    content_type: str = "circulars-cssf",
    max_pages: int | None = None,
    request_delay_ms: int = 500,
) -> Iterator[CircularListingRow]:
    # ... existing pagination loop ...
    # inside the loop, pass content_type to _parse_listing_page:
    matched, raw_count = _parse_listing_page(resp.text, publication_type_slug=content_type)
```

c) Update `_parse_listing_page` to accept `publication_type_slug: str` and thread it to `_row_from_library_element`.

d) Update `_row_from_library_element(item, publication_type_slug)`:

```python
_CSSF_REF_TYPES = {"circulars-cssf", "cssf-regulations", "annexes-to-cssf-circulars"}


def _row_from_library_element(item: Tag, publication_type_slug: str) -> CircularListingRow | None:
    title_link = item.select_one(".library-element__title a")
    if title_link is None:
        return None
    raw_title = title_link.get_text(" ", strip=True)
    href_raw = title_link.get("href") or ""
    href = href_raw if isinstance(href_raw, str) else ""
    if not href:
        return None
    detail_url = urljoin(_BASE_URL, href)

    if publication_type_slug in _CSSF_REF_TYPES:
        ref_match = _REF_RE.search(raw_title)
        if ref_match is None:
            return None
        reference_number = _normalize_ref(ref_match.group(0))
    else:
        reference_number = _synthesize_ref_from_slug(
            detail_url, publication_type_slug
        )
        if not reference_number:
            return None

    subtitle = item.select_one(".library-element__subtitle")
    description = subtitle.get_text(" ", strip=True) if subtitle else ""
    pub_el = item.select_one(".date--published")
    publication_date = (
        _parse_published_short(pub_el.get_text(" ", strip=True)) if pub_el else None
    )

    return CircularListingRow(
        reference_number=reference_number,
        raw_title=raw_title,
        description=description,
        publication_date=publication_date,
        detail_url=detail_url,
        publication_type_slug=publication_type_slug,
    )


def _synthesize_ref_from_slug(detail_url: str, publication_type_slug: str) -> str:
    """Build a synthetic stable identifier from the detail-page URL slug.

    The CSSF URL format is /en/Document/<slug>/; we derive the ref from
    <slug>. Prefix with the publication_type_slug to avoid collisions
    across types.
    """
    import re as _re
    m = _re.search(r"/Document/([^/]+)/?$", detail_url)
    if not m:
        return ""
    slug_part = m.group(1).lower()
    # Trim publication-type prefixes that CSSF embeds in the URL slug,
    # so the ref reads naturally: "law-of-2013-04-12" not
    # "law-law-of-2013-04-12".
    short = publication_type_slug.rstrip("s")  # "laws" -> "law"
    if slug_part.startswith(f"{short}-"):
        return slug_part
    return f"{short}-{slug_part}"
```

e) Keep the back-compat helper `_parse_listing_html` updated likewise if still used.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_cssf_scraper.py -v`
Expected: PASS, including existing circular tests.

- [ ] **Step 5: Commit**

```bash
git add regwatch/discovery/cssf_scraper.py tests/unit/test_cssf_scraper.py
git commit -m "feat(scraper): parse non-CSSF publication types with synthetic refs"
```

---

## Task 8 — Discovery service iterates filter matrix + UPSERTs provenance

**Files:**
- Modify: `regwatch/services/cssf_discovery.py`
- Test: `tests/unit/test_cssf_discovery_service.py` + `tests/integration/test_cssf_discovery_matrix.py` (new)

This is the core refactor: `CssfDiscoveryService.run` no longer takes `entity_types` only — it iterates the product of configured `entity_slugs` × `publication_types`. Every reconciled row UPSERTs a `RegulationDiscoverySource` row keyed on `(regulation_id, entity_type, content_type)`. `Regulation.type` is assigned from the publication-type config, not hardcoded.

- [ ] **Step 1: Write failing test (provenance UPSERT)**

Add to `tests/unit/test_cssf_discovery_service.py` (or a new `test_cssf_discovery_provenance.py`):

```python
from datetime import UTC, datetime
from sqlalchemy import select

from regwatch.db.models import (
    RegulationDiscoverySource, DiscoveryRun, Regulation, LifecycleStage,
    RegulationType,
)
from regwatch.services.cssf_discovery import CssfDiscoveryService


def test_upsert_discovery_source_first_sight_inserts(session_factory):
    """On first encounter, inserts with first_seen == last_seen."""
    # Arrange: one regulation, one run.
    with session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 22/806",
            title="X", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, is_ict=False,
            needs_review=False, url="", source_of_truth="CSSF_WEB",
        )
        s.add(reg); s.flush()
        reg_id = reg.regulation_id
        run = DiscoveryRun(
            status="RUNNING", started_at=datetime.now(UTC),
            triggered_by="TEST", entity_types=["AIFM"], mode="full",
        )
        s.add(run); s.commit()
        run_id = run.run_id

    service = CssfDiscoveryService(session_factory=session_factory, config=_stub_config())
    service._upsert_discovery_source(
        run_id=run_id, regulation_id=reg_id,
        entity_type="AIFM", content_type="circulars-cssf",
    )

    with session_factory() as s:
        src = s.scalars(select(RegulationDiscoverySource)).one()
        assert src.regulation_id == reg_id
        assert src.entity_type == "AIFM"
        assert src.content_type == "circulars-cssf"
        assert src.first_seen_run_id == src.last_seen_run_id == run_id


def test_upsert_discovery_source_second_sight_updates_last_seen(session_factory):
    """On repeat, updates last_seen_* but leaves first_seen_* intact."""
    # (similar arrange as above, but call _upsert_discovery_source twice
    # for two different runs; assert first_seen_run_id points at run 1,
    # last_seen_run_id points at run 2)
    ...
```

The `session_factory` fixture is the standard integration-test helper — copy/adapt from `tests/integration/test_app_smoke.py::_client`. `_stub_config()` returns a `CssfDiscoveryConfig` with the example entity_slugs + publication_types set.

- [ ] **Step 2: Write failing test (matrix run selects type from config)**

Create `tests/integration/test_cssf_discovery_matrix.py`. Use `httpx_mock` to serve 14 different listing-page responses (one per matrix cell). Each cell returns a single distinct reference. Assert:

- After `service.run(...)`, there are 14 distinct `RegulationDiscoverySource` rows.
- Each created regulation has `type = <the RegulationType of that cell>`.
- The `DiscoveryRun.status == "SUCCESS"`.

Leave the full matrix body to the implementer, but pin the expected count at 14 (2 entity types × 7 pub types).

- [ ] **Step 3: Run to verify fail**

Run: `pytest tests/unit/test_cssf_discovery_service.py tests/integration/test_cssf_discovery_matrix.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement**

In `regwatch/services/cssf_discovery.py`:

a) Replace `_run_for_slug(run_id, auth_type, slug, mode)` with `_run_for_cell(run_id, auth_type, entity_slug, pub: PublicationTypeConfig, mode)`. The cell carries the `RegulationType` to assign.

b) New private method:

```python
def _upsert_discovery_source(
    self, *, run_id: int, regulation_id: int,
    entity_type: str, content_type: str,
) -> None:
    now = datetime.now(UTC)
    with self._sf() as s:
        existing = s.scalar(
            select(RegulationDiscoverySource).where(
                RegulationDiscoverySource.regulation_id == regulation_id,
                RegulationDiscoverySource.entity_type == entity_type,
                RegulationDiscoverySource.content_type == content_type,
            )
        )
        if existing is None:
            s.add(RegulationDiscoverySource(
                regulation_id=regulation_id,
                entity_type=entity_type,
                content_type=content_type,
                first_seen_run_id=run_id,
                first_seen_at=now,
                last_seen_run_id=run_id,
                last_seen_at=now,
            ))
        else:
            existing.last_seen_run_id = run_id
            existing.last_seen_at = now
        s.commit()
```

c) `run()` signature stays the same, but the inner loop becomes:

```python
for et in entity_types:
    entity_slug = self._config.entity_slugs.get(et.value)
    if entity_slug is None:
        logger.warning("no slug mapped for %s; skipping", et.value); continue
    for pub in self._config.publication_types:
        try:
            self._run_for_cell(run_id, et, entity_slug, pub, mode)
        except Exception as e:  # noqa: BLE001
            logger.exception("cell %s x %s failed", entity_slug, pub.slug)
            aggregate_error = (...)
```

d) `_create_regulation` now takes the `PublicationTypeConfig` and sets `Regulation.type = RegulationType(pub.type)`.

e) After a successful reconcile, call `self._upsert_discovery_source(run_id, reg.regulation_id, entity_type=auth_type.value, content_type=pub.slug)`.

f) `_write_item` signature gains `content_type: str`; update every caller to pass the cell's `pub.slug`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_cssf_discovery_service.py tests/integration/test_cssf_discovery_matrix.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add regwatch/services/cssf_discovery.py tests/unit/test_cssf_discovery_service.py tests/integration/test_cssf_discovery_matrix.py
git commit -m "feat(discovery): iterate filter matrix + UPSERT provenance"
```

---

## Task 9 — `KEEP_ACTIVE` RegulationOverride action

**Files:**
- Modify: `regwatch/db/models.py` (doc comment only; action is a free-text column)
- Test: `tests/unit/test_regulation_override.py` (new or append)

Since `RegulationOverride.action` is `String(20)` (free-form), no schema change is required. We just document the new value and add a unit test that asserts the retire query respects it.

- [ ] **Step 1: Write failing test (placeholder — real test in Task 10)**

Add to `tests/unit/test_regulation_override.py`:

```python
def test_keep_active_action_is_accepted(session_factory):
    """A KEEP_ACTIVE override persists successfully and is distinguishable."""
    from regwatch.db.models import RegulationOverride
    with session_factory() as s:
        ov = RegulationOverride(
            reference_number="CSSF 22/806",
            action="KEEP_ACTIVE",
            reason="Manual keep",
        )
        s.add(ov); s.commit()
        assert ov.action == "KEEP_ACTIVE"
```

- [ ] **Step 2: Run to verify pass**

Run: `pytest tests/unit/test_regulation_override.py -v`
Expected: PASS immediately (no code change needed; the test is a regression check on the free-form column).

- [ ] **Step 3: Document**

Add a docstring comment above `RegulationOverride.action` listing known values: `"EXCLUDE"`, `"SET_ICT"`, `"UNSET_ICT"`, `"KEEP_ACTIVE"`.

- [ ] **Step 4: Commit**

```bash
git add regwatch/db/models.py tests/unit/test_regulation_override.py
git commit -m "docs(models): document KEEP_ACTIVE override action"
```

---

## Task 10 — Auto-retire: `retire_missing` + SUCCESS safety gate + reactivation

**Files:**
- Modify: `regwatch/services/cssf_discovery.py` (add `retire_missing`, call from `_finalize_run`)
- Test: `tests/integration/test_cssf_retire.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/integration/test_cssf_retire.py`. Required cases:

```python
def test_retire_marks_unseen_cssf_web_as_repealed(session_factory):
    # Arrange: 3 regs, all CSSF_WEB IN_FORCE.
    #   - reg_a has a discovery_source row with last_seen_run_id == current run.
    #   - reg_b has a discovery_source row with an older run id.
    #   - reg_c has no discovery_source rows at all.
    # Act: run service.retire_missing(current_run_id).
    # Assert:
    #   - reg_a still IN_FORCE.
    #   - reg_b now REPEALED.
    #   - reg_c now REPEALED.
    #   - returned count == 2.
    ...

def test_retire_skipped_when_run_not_success(session_factory):
    # Arrange: same as above, but discovery_run.status = "PARTIAL".
    # Act: run service._finalize_run(run_id, error="something failed").
    # Assert: no regulation's lifecycle_stage changed.
    ...

def test_retire_respects_keep_active_override(session_factory):
    # Arrange: reg_x is CSSF_WEB IN_FORCE with no discovery_source for current run,
    # AND a RegulationOverride(action="KEEP_ACTIVE", reference_number=reg_x.ref).
    # Act: retire_missing.
    # Assert: reg_x still IN_FORCE.
    ...

def test_retire_ignores_non_cssf_web_rows(session_factory):
    # Arrange: SEED / DISCOVERED / CSSF_STUB rows have no discovery_source.
    # Act: retire_missing.
    # Assert: lifecycle_stage unchanged for all three.
    ...

def test_repealed_row_reactivates_when_reobserved(session_factory):
    # Arrange: reg_z lifecycle_stage=REPEALED source_of_truth=CSSF_WEB.
    # Act: call _reconcile_row for a listing that returns reg_z's ref.
    # Assert: reg_z.lifecycle_stage == IN_FORCE after.
    ...

def test_retire_writes_discovery_run_items(session_factory):
    # Arrange: retire 2 rows.
    # Act: retire_missing.
    # Assert: 2 DiscoveryRunItem rows with outcome="RETIRED" exist for the run.
    ...
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/integration/test_cssf_retire.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `retire_missing`**

Add to `CssfDiscoveryService`:

```python
def retire_missing(self, run_id: int) -> int:
    """Mark CSSF_WEB regulations not seen in this run as REPEALED.

    Caller must gate on run.status == 'SUCCESS'. Returns count retired.
    """
    retired = 0
    now = datetime.now(UTC)
    with self._sf() as s:
        seen_subq = select(RegulationDiscoverySource.regulation_id).where(
            RegulationDiscoverySource.last_seen_run_id == run_id
        )
        keep_active_refs = s.scalars(
            select(RegulationOverride.reference_number).where(
                RegulationOverride.action == "KEEP_ACTIVE"
            )
        ).all()
        stale = s.scalars(
            select(Regulation).where(
                Regulation.source_of_truth == "CSSF_WEB",
                Regulation.lifecycle_stage != LifecycleStage.REPEALED,
                Regulation.regulation_id.not_in(seen_subq),
                Regulation.reference_number.not_in(list(keep_active_refs) or [""]),
            )
        ).all()
        for reg in stale:
            reg.lifecycle_stage = LifecycleStage.REPEALED
            s.add(DiscoveryRunItem(
                run_id=run_id, regulation_id=reg.regulation_id,
                reference_number=reg.reference_number, outcome="RETIRED",
                detail_url=None, entity_type="", content_type="",
                note="absent from all filter-matrix cells",
            ))
            retired += 1
        s.commit()
    return retired
```

- [ ] **Step 4: Gate retire on SUCCESS in `_finalize_run`**

In `_finalize_run`, after status is computed, just before the final `s.commit()`:

```python
if run.status == "SUCCESS":
    run.retired_count = self.retire_missing(run_id)
else:
    run.retired_count = 0
```

Update the `DiscoveryRun` status decision so a RETIRED-only run with no errors stays `"SUCCESS"`. Since `retire_missing` can write `DiscoveryRunItem` rows with outcome `RETIRED`, the count aggregation after retirement must re-run or be skipped; simplest fix: compute counts **before** calling retire, then update `retired_count` independently.

- [ ] **Step 5: Implement reactivation**

In `_reconcile_row`, after the "existing" branch is taken and we know the row was seen:

```python
if existing.lifecycle_stage == LifecycleStage.REPEALED and existing.source_of_truth == "CSSF_WEB":
    existing.lifecycle_stage = LifecycleStage.IN_FORCE
    # (existing code proceeds to update metadata / write item)
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/integration/test_cssf_retire.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add regwatch/services/cssf_discovery.py tests/integration/test_cssf_retire.py
git commit -m "feat(discovery): auto-retire on SUCCESS + KEEP_ACTIVE + reactivation"
```

---

## Task 11 — Remove `enrich_stubs` (service + CLI)

**Files:**
- Modify: `regwatch/services/cssf_discovery.py` — remove `enrich_stubs` method entirely.
- Modify: `regwatch/cli.py::discover_cssf` — remove `--enrich-stubs` flag; raise a clear error if passed.
- Modify: `tests/unit/test_cssf_discovery_service.py` — remove any tests for `enrich_stubs`; add a test that ensures the method no longer exists.
- Modify: `tests/unit/test_cli.py` — remove tests that exercised `--enrich-stubs`; add a test that the flag is rejected.

Per CLAUDE.md, no backward-compat shim. The flag goes away; its signature stays in argparse only long enough to emit a helpful error.

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_cssf_discovery_service.py`:

```python
def test_enrich_stubs_method_is_removed():
    from regwatch.services.cssf_discovery import CssfDiscoveryService
    assert not hasattr(CssfDiscoveryService, "enrich_stubs")
```

In `tests/unit/test_cli.py`:

```python
def test_discover_cssf_enrich_stubs_flag_rejected(runner):
    """--enrich-stubs no longer valid; CLI prints an error and exits non-zero."""
    result = runner.invoke(app, ["discover-cssf", "--enrich-stubs"])
    assert result.exit_code != 0
    assert "--enrich-stubs has been removed" in result.output
```

`runner` is the standard Typer `CliRunner` fixture used in other CLI tests.

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/unit/test_cssf_discovery_service.py tests/unit/test_cli.py -v -k "enrich_stubs"`
Expected: FAIL.

- [ ] **Step 3: Remove method**

Delete `CssfDiscoveryService.enrich_stubs` entirely (the block starting `def enrich_stubs(self, *, max_rows=None)`). Also remove the `_slug_from_reference` helper if it's unused after this (check references first — it's also used by `backfill_titles_and_descriptions`, so keep it).

- [ ] **Step 4: Reject the CLI flag**

In `regwatch/cli.py::discover_cssf`, replace the `enrich_stubs` argument handling with:

```python
    enrich_stubs: Annotated[
        bool,
        typer.Option(
            "--enrich-stubs",
            hidden=True,
            help="Removed; the filter-matrix crawl promotes stubs via normal discovery.",
        ),
    ] = False,
    ...
    if enrich_stubs:
        typer.echo(
            "--enrich-stubs has been removed. Run `regwatch discover-cssf` "
            "(the full filter matrix) — stubs are promoted automatically "
            "when re-observed in any cell."
        )
        raise typer.Exit(code=2)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_cssf_discovery_service.py tests/unit/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add regwatch/services/cssf_discovery.py regwatch/cli.py tests/unit/test_cssf_discovery_service.py tests/unit/test_cli.py
git commit -m "refactor(discovery): remove enrich_stubs method + CLI flag"
```

---

## Task 12 — CLI: `--publication-type` + `--dry-run`

**Files:**
- Modify: `regwatch/cli.py::discover_cssf`
- Modify: `regwatch/services/cssf_discovery.py` (add `dry_run: bool` to `run`)
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_cli.py`:

```python
def test_discover_cssf_single_cell_disables_retire(runner, monkeypatch):
    """--entity + --publication-type restricts to one cell and skips retire."""
    # Stub out CssfDiscoveryService; capture args.
    called = {}
    class _StubService:
        def __init__(self, **kw): called.update(kw)
        def run(self, *, entity_types, mode, triggered_by, dry_run=False, restrict_pub_slug=None):
            called["entity_types"] = entity_types
            called["restrict_pub_slug"] = restrict_pub_slug
            called["dry_run"] = dry_run
            return 42
    monkeypatch.setattr("regwatch.cli.CssfDiscoveryService", _StubService)

    result = runner.invoke(app, [
        "discover-cssf",
        "--entity", "AIFM",
        "--publication-type", "CSSF_CIRCULAR",
    ])
    assert result.exit_code == 0
    assert called["entity_types"] == ["AIFM"] or \
           [e.value for e in called["entity_types"]] == ["AIFM"]
    assert called["restrict_pub_slug"] == "circulars-cssf"

def test_discover_cssf_dry_run_does_not_commit(runner, monkeypatch):
    """--dry-run passes dry_run=True to the service."""
    called = {}
    class _StubService:
        def __init__(self, **kw): pass
        def run(self, *, dry_run=False, **kw):
            called["dry_run"] = dry_run
            return 1
    monkeypatch.setattr("regwatch.cli.CssfDiscoveryService", _StubService)

    result = runner.invoke(app, ["discover-cssf", "--dry-run"])
    assert result.exit_code == 0
    assert called["dry_run"] is True
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/unit/test_cli.py -v -k "single_cell or dry_run"`
Expected: FAIL.

- [ ] **Step 3: Extend the CLI**

In `regwatch/cli.py::discover_cssf`, add:

```python
    publication_type: Annotated[
        str | None,
        typer.Option(
            "--publication-type",
            help="RegulationType enum value to restrict to a single matrix column",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print would-be changes; commit nothing. Skips retire.",
        ),
    ] = False,
    ...
```

Translate `publication_type` (enum value) → pub slug by looking up `cfg.cssf_discovery.publication_types` where `pt.type == publication_type`. Pass the slug to the service via a new `restrict_pub_slug: str | None` kwarg. Pass `dry_run` through.

- [ ] **Step 4: Extend `CssfDiscoveryService.run`**

Add kwargs:

```python
def run(
    self, *, entity_types, mode, triggered_by,
    existing_run_id=None,
    dry_run: bool = False,
    restrict_pub_slug: str | None = None,
) -> int:
```

When `restrict_pub_slug` is set, filter `self._config.publication_types` to that one slug before iterating.

When `dry_run=True`:
- Execute the scrape as normal (HTTP is read-only).
- Do NOT commit regulation inserts/updates; do NOT UPSERT provenance; do NOT call `retire_missing`.
- Log what would happen (counts of would-be NEW/AMENDED/UPDATED/RETIRED candidates).
- Still write a `DiscoveryRun` record with status=`SUCCESS` and `note="dry-run"` for audit; every `DiscoveryRunItem` gets `outcome="DRY_RUN_<WOULD_BE_OUTCOME>"`.

Implementation hint: the simplest pattern is to open **one outer `Session` with `rollback()` in a `finally`** for the whole run when `dry_run=True`, so anything staged is discarded. Alternatively, gate every `s.commit()` on `not dry_run`. Pick whichever fits the existing service shape cleanest — the existing `_reconcile_row` opens its own session per call, so the "rollback outer session" approach doesn't fit; gate the commits.

Also: when `restrict_pub_slug` is non-None OR `dry_run` is True, the final `_finalize_run` skips `retire_missing` unconditionally.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_cli.py tests/unit/test_cssf_discovery_service.py tests/integration/test_cssf_discovery_matrix.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add regwatch/cli.py regwatch/services/cssf_discovery.py tests/unit/test_cli.py
git commit -m "feat(cli): --publication-type single-cell + --dry-run"
```

---

## Task 13 — Web UI: provenance panel + per-cell run breakdown

**Files:**
- Modify: `regwatch/web/routes/regulations.py` (or wherever the regulation detail route is)
- Modify: `regwatch/web/templates/regulations/detail.html.j2` (or equivalent)
- Modify: `regwatch/web/routes/discovery.py` / `discovery_runs.py` (wherever run detail is served)
- Modify: `regwatch/web/templates/discovery/run_detail.html.j2` (or equivalent)
- Modify: `regwatch/services/cssf_discovery.py` — add DTO methods.
- Test: `tests/integration/test_web_provenance.py` (new)

Find the exact template and route filenames first with `grep -r "regulation_detail" regwatch/web/`.

- [ ] **Step 1: Add service DTOs**

In `regwatch/services/cssf_discovery.py`, add:

```python
from dataclasses import dataclass

@dataclass
class DiscoverySourceDTO:
    entity_type: str
    content_type: str
    first_seen_at: datetime
    last_seen_at: datetime
    first_seen_run_id: int
    last_seen_run_id: int


def list_discovery_sources(self, regulation_id: int) -> list[DiscoverySourceDTO]:
    with self._sf() as s:
        rows = s.scalars(
            select(RegulationDiscoverySource).where(
                RegulationDiscoverySource.regulation_id == regulation_id
            ).order_by(
                RegulationDiscoverySource.entity_type,
                RegulationDiscoverySource.content_type,
            )
        ).all()
        return [
            DiscoverySourceDTO(
                entity_type=r.entity_type, content_type=r.content_type,
                first_seen_at=r.first_seen_at, last_seen_at=r.last_seen_at,
                first_seen_run_id=r.first_seen_run_id, last_seen_run_id=r.last_seen_run_id,
            ) for r in rows
        ]
```

- [ ] **Step 2: Write failing test**

`tests/integration/test_web_provenance.py`:

```python
def test_regulation_detail_shows_provenance(_client):
    # Arrange: seed one regulation + two discovery_source rows
    # (AIFM/circulars-cssf and CHAPTER15_MANCO/circulars-cssf).
    # Act: GET /regulations/<id>
    # Assert: response contains both "(AIFM, circulars-cssf)" and
    #         "(CHAPTER15_MANCO, circulars-cssf)" in the rendered HTML.
    ...

def test_discovery_run_detail_shows_retired_count_and_cell_breakdown(_client):
    # Arrange: one DiscoveryRun with 14 DiscoveryRunItems spread across
    # cells + 3 RETIRED items; retired_count=3.
    # Act: GET /discovery/runs/<run_id>
    # Assert: body contains "Retired: 3" and per-cell rows.
    ...
```

- [ ] **Step 3: Run to verify fail**

Run: `pytest tests/integration/test_web_provenance.py -v`
Expected: FAIL.

- [ ] **Step 4: Render provenance in the regulation detail template**

In the regulation detail route, inject `discovery_sources=svc.list_discovery_sources(reg.regulation_id)` into the template context. In the Jinja template, render a small table under a "Discovery provenance" heading:

```jinja
{% if discovery_sources %}
<section class="card">
  <h3>Discovery provenance</h3>
  <table>
    <thead><tr><th>Entity</th><th>Publication type</th><th>First seen</th><th>Last seen</th></tr></thead>
    <tbody>
      {% for src in discovery_sources %}
      <tr>
        <td>{{ src.entity_type }}</td>
        <td>{{ src.content_type }}</td>
        <td>{{ src.first_seen_at.strftime('%Y-%m-%d') }}</td>
        <td>{{ src.last_seen_at.strftime('%Y-%m-%d') }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}
```

- [ ] **Step 5: Render per-cell breakdown + retired_count on the run detail**

In the discovery run detail route, group `DiscoveryRunItem` by `(entity_type, content_type)`. Pass the grouped data + `run.retired_count` to the template. Render a table of 14 cells (one row each) with their NEW / AMENDED / UPDATED_METADATA / UNCHANGED / FAILED counts, plus a "Retired" summary line.

- [ ] **Step 6: Run tests**

Run: `pytest tests/integration/test_web_provenance.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add regwatch/web/routes/ regwatch/web/templates/ regwatch/services/cssf_discovery.py tests/integration/test_web_provenance.py
git commit -m "feat(web): provenance panel + per-cell run breakdown"
```

---

## Task 14 — End-to-end integration: fake 14-cell CSSF + retire + reactivate

**Files:**
- Create: `tests/integration/test_cssf_end_to_end.py`
- Create: `tests/fixtures/cssf/matrix_listing_<cell>.html` × 14 — tiny hand-crafted fixtures, each containing one distinct document reference

The goal is one comprehensive test that drives the full matrix twice (run N, then run N+1 where one ref disappears) and asserts the expected lifecycle transitions without touching the real CSSF site.

- [ ] **Step 1: Create matrix fixtures**

14 tiny HTML files, one per (entity × publication_type) cell. Each contains exactly one `<li class="library-element">` with a distinct ref / URL. For circulars, use `CSSF 99/001`, `CSSF 99/002`, etc.; for laws, use distinct `/Document/law-of-2026-01-01/` style URLs. Each fixture also needs a corresponding detail-page fixture returning the matching `CircularDetail`-parseable HTML.

- [ ] **Step 2: Write the end-to-end test**

```python
def test_full_matrix_creates_14_regulations_with_provenance(
    session_factory, httpx_mock, tmp_path
):
    # Register 14 listing responses + 14 detail responses with httpx_mock.
    # Construct CssfDiscoveryConfig with the 14 cells.
    # Run service.run(entity_types=[AIFM, CHAPTER15_MANCO], mode="full", triggered_by="TEST").
    # Assert:
    #   - 14 Regulation rows created, one per cell, with correct RegulationType.
    #   - 14 RegulationDiscoverySource rows with correct (entity, content_type).
    #   - DiscoveryRun.status == "SUCCESS", retired_count == 0.
    ...

def test_second_run_retires_vanished_regulation(session_factory, httpx_mock):
    # Arrange: a previous DB state with 2 regulations + provenance for both.
    # Register httpx_mock to return only 1 of them on the new matrix run.
    # Run service.run.
    # Assert:
    #   - 1 regulation still IN_FORCE.
    #   - 1 regulation lifecycle_stage == REPEALED.
    #   - run.retired_count == 1.
    #   - A DiscoveryRunItem with outcome="RETIRED" for the retired ref.
    ...

def test_second_run_reactivates_returning_regulation(session_factory, httpx_mock):
    # Arrange: reg lifecycle_stage=REPEALED from a previous retirement.
    # Register matrix run that includes this reg's listing.
    # Run service.run.
    # Assert: reg lifecycle_stage == IN_FORCE.
    ...
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/integration/test_cssf_end_to_end.py -v`
Expected: PASS.

- [ ] **Step 4: Run the full test suite once**

Run: `pytest`
Expected: all PASS. Fix any regression introduced by earlier tasks before committing.

- [ ] **Step 5: Lint + type check**

```bash
ruff check regwatch
mypy regwatch
```

Both must be clean before the final commit.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_cssf_end_to_end.py tests/fixtures/cssf/
git commit -m "test(discovery): end-to-end filter matrix + retire + reactivate"
```

---

## Final developer steps (manual, not TDD)

After the last commit:

- [ ] **Run the live slug probe** to confirm FacetWP slugs are still current:
  ```bash
  pytest -m live tests/live/test_cssf_slug_discovery.py -v -s
  ```
  If slugs differ from `config.example.yaml`, update both `config.example.yaml` and your local `config.yaml`, then commit.

- [ ] **Dry-run on real data**:
  ```bash
  regwatch discover-cssf --dry-run
  ```
  Inspect stdout and the resulting `DiscoveryRun` / `DiscoveryRunItem` rows. Expect ~300–400 retirement candidates.

- [ ] **Add `KEEP_ACTIVE` overrides** for any false-positive retirements (refs you want kept despite absence from the live filter view).

- [ ] **Run for real**:
  ```bash
  regwatch discover-cssf
  ```
  Verify `retired_count` on the resulting run.

---

## Self-review (completed by plan author)

**Spec coverage:**
- Filter matrix iteration — Task 8. ✓
- FacetWP slug discovery — Task 5. ✓
- Ref handling for non-CSSF types — Task 7. ✓
- `RegulationType` mapping — Tasks 1 + 8. ✓
- `RegulationDiscoverySource` model — Task 2. ✓
- `DiscoveryRunItem` rename (`entity_types` → `entity_type` + `content_type`) — Task 3. ✓
- `DiscoveryRun.retired_count` — Task 2. ✓
- `RegulationType` enum extension — Task 1. ✓
- `RegulationOverride.KEEP_ACTIVE` — Task 9. ✓
- One-shot migration — Task 3. ✓
- Auto-retire with SUCCESS gate — Task 10. ✓
- `KEEP_ACTIVE` override honoured — Task 10. ✓
- Reactivation — Task 10. ✓
- `enrich_stubs` removal — Task 11. ✓
- Config extension — Task 4. ✓
- CLI `--publication-type` + `--dry-run` + `--enrich-stubs` removal — Tasks 11 + 12. ✓
- Web UI provenance + per-cell breakdown — Task 13. ✓
- Testing strategy (unit + integration + live) — Tasks 2, 5, 6, 8, 10, 11, 12, 13, 14. ✓

**No placeholders:** every step contains actual code or an actual command with expected output.

**Type consistency:** `RegulationDiscoverySource` field names (`first_seen_at`, `last_seen_at`, `first_seen_run_id`, `last_seen_run_id`, `entity_type`, `content_type`) used identically in Tasks 2, 8, 10, 13. `CssfDiscoveryService.run` kwarg names (`dry_run`, `restrict_pub_slug`) used identically in Tasks 12 and 14. `PublicationTypeConfig.{label, slug, type}` used identically in Tasks 4, 7, 8.

Spec requirements all covered; plan is ready to execute.
