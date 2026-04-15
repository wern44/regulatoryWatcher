# CSSF Filter-Matrix Crawl — Design

**Date:** 2026-04-15
**Status:** Approved — ready to plan
**Supersedes (partially):** `2026-04-14-cssf-discovery-design.md` — keeps its scraper/service skeleton but replaces the single-slug crawl with a filter matrix and adds auto-retire.

## Problem

The current `CssfDiscoveryService` crawls CSSF with a single filter (`fwp_entity_type=<slug>` + a hardcoded `fwp_content_type=circulars-cssf`) and additionally recurses through the amendment graph via `enrich_stubs`, promoting referenced circulars to first-class rows. This produces two defects:

1. **Stale items pollute the catalog.** When the user filters the live CSSF site by AIFM, they see far fewer circulars than we have imported. Many of our rows are superseded / amended-out / never-applicable items that were pulled in by stub recursion, not by the authoritative CSSF filter.
2. **No filter provenance is recorded.** We cannot answer "which CSSF listing filter surfaced this regulation?" from the catalog — crawl results lose their origin, and `DiscoveryRunItem.entity_types` is a JSON list with no content-type column.

## Goal

Replace the single-slug crawl with a **2 × 7 filter matrix** (two `AuthorizationType` values × seven publication types), record the filter that surfaced each regulation, and auto-retire rows no longer visible in any current filter view. Drop recursive stub promotion.

## Non-goals

- **EU regulations / EUR-Lex** — handled by a separate source plugin and is out of scope for this spec. Will be revisited in a follow-up once the CSSF matrix is stable.
- **Adding new entity types** — the monitored company (Union Investment Luxembourg S.A.) is licensed only as an AIFM and a Chapter 15 management company. The `AuthorizationType` enum is unchanged.
- **Historical `DiscoveryRunItem` data synthesis** — we migrate the existing single-element `entity_types` JSON arrays to the new scalar `entity_type`; we do not attempt to reconstruct `content_type` for past runs.
- **Graph edges for unknown refs** — `RegulationLifecycleLink` continues to require two regulation rows on each edge. Cross-references to unknown refs still create `CSSF_STUB` rows as placeholders, but stubs never auto-promote to `CSSF_WEB`.

## Filter matrix

| | CSSF circular | CSSF regulation | Law | Grand-ducal reg. | Ministerial reg. | Annex to CSSF circular | Professional standard |
|---|---|---|---|---|---|---|---|
| AIFM | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| CHAPTER15_MANCO | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Each run executes 14 independent scraper passes, one per cell. Each pass records its (entity_type, content_type) provenance on every regulation it surfaces.

### FacetWP slug discovery

Only `circulars-cssf` is confirmed today. The other six FacetWP `fwp_content_type` slugs are unknown and must be discovered. A live probe test (`tests/live/test_cssf_slug_discovery.py`, marked `@pytest.mark.live`) fetches the listing page, reads the rendered `fwp_content_type` `<select>` options, asserts the seven labels the user specified are present, and writes the label→slug mapping to `regwatch/discovery/cssf_scraper.py` as a named constant. HTML fixtures per publication type go under `tests/fixtures/cssf/<slug>/` per the existing convention.

### Reference numbering for non-CSSF publication types

`_REF_RE` recognises CSSF/IML/BCL reference numbers only. Laws and grand-ducal/ministerial regulations use different conventions (e.g. `Loi du 5 avril 1993`). For these rows:

- Identification keys off the listing row's `<p class="library-element__type">` label plus the detail-page URL slug, not the ref regex.
- `Regulation.reference_number` is populated with a synthetic slug-derived identifier (e.g. `law-of-1993-04-05`). The original human-readable reference goes into `title`.
- The detail URL is the stable key — if the slug changes, the regulation is treated as a new row and the old one retires.

### `RegulationType` mapping

| Publication type (UI label) | FacetWP slug (TBD via live probe) | `RegulationType` |
|---|---|---|
| CSSF circular | `circulars-cssf` | `CSSF_CIRCULAR` |
| CSSF regulation | *(probe)* | `CSSF_REGULATION` |
| Law | *(probe)* | `LU_LAW` |
| Grand-ducal regulation | *(probe)* | `LU_GRAND_DUCAL_REGULATION` *(new enum value)* |
| Ministerial regulation | *(probe)* | `LU_MINISTERIAL_REGULATION` *(new enum value)* |
| Annex to a CSSF circular | *(probe)* | `CSSF_CIRCULAR_ANNEX` *(new enum value)* |
| Professional standard | *(probe)* | `PROFESSIONAL_STANDARD` *(new enum value)* |

## Data model

### New: `regulation_discovery_source`

```python
class RegulationDiscoverySource(Base):
    __tablename__ = "regulation_discovery_source"
    source_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40))       # AuthorizationType enum value
    content_type: Mapped[str] = mapped_column(String(60))      # FacetWP slug
    first_seen_run_id: Mapped[int] = mapped_column(ForeignKey("discovery_run.run_id"))
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_seen_run_id: Mapped[int] = mapped_column(ForeignKey("discovery_run.run_id"))
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    __table_args__ = (
        UniqueConstraint("regulation_id", "entity_type", "content_type",
                         name="uq_discovery_source_reg_entity_content"),
    )
```

Each filter-matrix pass UPSERTs one row per regulation it saw in that cell: insert on first sight, update `last_seen_*` on every subsequent sighting.

### Changed: `DiscoveryRunItem`

- `entity_types: list[str] (JSON)` → `entity_type: str (String(40))` — each item now belongs to exactly one matrix cell.
- **New:** `content_type: str (String(60))` — the FacetWP slug for the cell.
- **New:** `outcome` values include `"RETIRED"`.

### Changed: `DiscoveryRun`

- **New:** `retired_count: int` alongside existing counters.
- `entity_types: list[str]` stays — this is still the set of entities covered by the run.

### Changed: `RegulationType` enum

Add: `CSSF_CIRCULAR_ANNEX`, `PROFESSIONAL_STANDARD`, `LU_GRAND_DUCAL_REGULATION`, `LU_MINISTERIAL_REGULATION`.

### Changed: `RegulationOverride`

Add a new `action` value: `"KEEP_ACTIVE"`. A regulation with this override is never retired, even if absent from every filter-matrix cell.

### Migration

`regwatch/db/migrations.py` gains a one-shot function invoked during `init-db` / app startup that:

1. Detects the old `discovery_run_item.entity_types` JSON column if present.
2. Creates the new scalar `entity_type` and `content_type` columns.
3. Copies `entity_types[0]` → `entity_type` for each row; sets `content_type = 'circulars-cssf'` (the only value used pre-migration).
4. Drops `entity_types`.

Since the project uses `Base.metadata.create_all` (not Alembic per `CLAUDE.md`), `RegulationDiscoverySource` and the new columns/enum values on existing tables come up automatically on first engine connect. The explicit migration is only needed for the rename + historical data copy.

## Auto-retire algorithm

At the end of every filter-matrix run, after all 14 passes complete, the service runs:

```python
def retire_missing(run_id: int, session: Session) -> int:
    """Mark CSSF_WEB regulations not seen in this run as REPEALED.

    Safety: callers must gate this on run.status == 'SUCCESS' — if any pass
    failed, retirement is skipped. Otherwise a transient CSSF outage would
    wipe the catalog.
    """
    seen = select(RegulationDiscoverySource.regulation_id).where(
        RegulationDiscoverySource.last_seen_run_id == run_id
    )
    keep_active = select(RegulationOverride.reference_number).where(
        RegulationOverride.action == "KEEP_ACTIVE"
    )
    stale = session.scalars(
        select(Regulation).where(
            Regulation.source_of_truth == "CSSF_WEB",
            Regulation.lifecycle_stage != LifecycleStage.REPEALED,
            Regulation.regulation_id.not_in(seen),
            Regulation.reference_number.not_in(keep_active),
        )
    ).all()
    for reg in stale:
        reg.lifecycle_stage = LifecycleStage.REPEALED
        session.add(DiscoveryRunItem(
            run_id=run_id,
            regulation_id=reg.regulation_id,
            reference_number=reg.reference_number,
            outcome="RETIRED",
            entity_type="",       # not scoped to a cell
            content_type="",      # not scoped to a cell
            note="absent from all filter-matrix cells",
        ))
    return len(stale)
```

### Invariants

- **Only `source_of_truth == "CSSF_WEB"` rows** are touched. `SEED`, `DISCOVERED`, `CSSF_STUB` are immune.
- **Retire only runs on `status == "SUCCESS"`.** Any `PARTIAL` / `FAILED` run skips retirement. This is the single most important safety rule — a CSSF outage must not produce mass retirement.
- **`RegulationOverride.action == "KEEP_ACTIVE"`** wins over retirement for explicit manual overrides.
- **Reactivation** is automatic: if a previously-`REPEALED` regulation is re-observed by any cell in a new run, `_refresh_metadata` flips it back to `IN_FORCE`.

## Drop recursive stub promotion

- `CssfDiscoveryService.enrich_stubs` — **removed**.
- `regwatch discover-cssf --enrich-stubs` — **removed**; the flag raises a clear error directing the user to the full filter-matrix crawl.
- `_ensure_amendment_stubs` — **kept**. Cross-references from a fetched detail page still create `CSSF_STUB` placeholder rows so that `RegulationLifecycleLink` edges can point at them. These stubs never auto-promote; if a stub ref also appears in a filter-matrix cell in a subsequent run, the normal NEW / existing-row path upgrades its `source_of_truth` to `CSSF_WEB`.

## Config & CLI

### `config.yaml` / `config.example.yaml`

```yaml
cssf_discovery:
  base_url: https://www.cssf.lu/en/regulatory-framework/
  request_delay_ms: 500
  user_agent: RegulatoryWatcher/1.0
  entity_slugs:
    AIFM: aifms
    CHAPTER15_MANCO: management-companies-chapter-15
  publication_types:
    - { label: "CSSF circular",            slug: circulars-cssf,              type: CSSF_CIRCULAR }
    - { label: "CSSF regulation",          slug: cssf-regulations,            type: CSSF_REGULATION }
    - { label: "Law",                      slug: laws,                        type: LU_LAW }
    - { label: "Grand-ducal regulation",   slug: grand-ducal-regulations,     type: LU_GRAND_DUCAL_REGULATION }
    - { label: "Ministerial regulation",   slug: ministerial-regulations,     type: LU_MINISTERIAL_REGULATION }
    - { label: "Annex to a CSSF circular", slug: annexes-to-cssf-circulars,   type: CSSF_CIRCULAR_ANNEX }
    - { label: "Professional standard",    slug: professional-standards,      type: PROFESSIONAL_STANDARD }
```

Slug values above are placeholders — the live probe fills in the real values and the spec's first implementation task is to confirm them.

The unused `CssfDiscoveryConfig.content_types` field is removed — no backward-compat shim per project conventions.

### CLI

| Command | Behaviour |
|---|---|
| `regwatch discover-cssf` | Run the full 14-cell matrix; retire on SUCCESS. |
| `regwatch discover-cssf --entity AIFM --publication-type CSSF_CIRCULAR` | Run a single matrix cell. Does **not** retire (single-cell crawl cannot conclude a regulation is globally absent). |
| `regwatch discover-cssf --dry-run` | Execute the full matrix; print what would be created/amended/retired; commit nothing. |
| `regwatch discover-cssf --backfill` | Unchanged. Metadata refresh only. |
| `regwatch discover-cssf --reclassify` | Unchanged. ICT flag recompute only. |
| `regwatch discover-cssf --enrich-stubs` | **Removed.** Raises an error with a pointer to the full crawl. |

### Web UI

- **Regulation detail page** gains a "Discovery provenance" panel: a table of `(entity_type, content_type, first_seen, last_seen)` rows from `regulation_discovery_source`.
- **Discovery run detail page** shows a 14-row breakdown (one per matrix cell) with per-cell NEW / AMENDED / UNCHANGED / FAILED counts, plus the run-level `retired_count`.

## Rollout sequence

1. Ship code + migration (auto on startup via `create_all` + `migrations.py`).
2. Run `pytest -m live tests/live/test_cssf_slug_discovery.py` to populate the seven FacetWP slugs. Commit the result.
3. Run `regwatch discover-cssf --dry-run`. Inspect output: expected ~300–400 retirement candidates out of 551 current `CSSF_WEB` rows.
4. Review the retirement list; for any false positives, add `RegulationOverride` rows with `action="KEEP_ACTIVE"`.
5. Run `regwatch discover-cssf` for real. `retired_count` on the run row records the outcome.

## Testing strategy

- **Unit tests** against HTML fixtures per publication type (seven fixtures under `tests/fixtures/cssf/<slug>/`): parse a listing page + at least one detail page per type. Existing fixture-based tests continue to cover `circulars-cssf`.
- **Unit tests** for `RegulationDiscoverySource` UPSERT semantics: first-sight inserts with `first_seen == last_seen`; repeat-sight updates `last_seen_*` without touching `first_seen_*`; unique constraint enforced.
- **Unit tests** for the retire safety invariant: `retire_missing` is a no-op when `run.status != "SUCCESS"`; honours `KEEP_ACTIVE` overrides; never touches non-`CSSF_WEB` rows.
- **Unit tests** for reactivation: a `REPEALED` row re-observed in a new run flips to `IN_FORCE`.
- **Integration test** for the full matrix against a `pytest-httpx`-backed fake CSSF, with 14 different listing URL responses; assert correct per-cell provenance + end-to-end retirement of a row present in run N but absent in run N+1.
- **Live probe** (`@pytest.mark.live`, excluded from default `pytest`): confirms slug values are still valid; fails noisily on DOM change.

## Open questions

None.
