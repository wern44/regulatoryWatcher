# CSSF Discovery — Design

**Date:** 2026-04-14
**Status:** Approved — ready to plan

## Goal

Replace LLM-guesswork discovery with authoritative scraping of
`https://www.cssf.lu/en/regulatory-framework/`, filtered per authorization
type, with amendment-graph tracking and per-run diff reporting.

## Non-goals

- Ingesting update events (the existing RSS fetch pipeline still handles that).
- Scraping non-CSSF sources (EU regulations come via EUR-Lex; this is CSSF-specific).
- Replacing the existing `DiscoveryService` LLM-classify pass — we use its `classify_catalog` as a fallback for rows we can't classify deterministically.

## Architecture

### Module: `regwatch/discovery/cssf_scraper.py` — pure HTTP

Two public functions, no DB.

```python
@dataclass
class CirculatListingRow:
    reference_number: str        # "CSSF 22/806" (no amendment parenthetical)
    raw_title: str               # "Circular CSSF 22/806 (as amended by CSSF 25/883) on outsourcing"
    description: str             # short scope text from the listing row
    publication_date: date
    detail_url: str              # absolute URL

@dataclass
class CircularDetail:
    reference_number: str
    clean_title: str             # title with amendment parenthetical stripped
    amended_by_refs: list[str]   # parsed from title + Related documents
    amends_refs: list[str]
    supersedes_refs: list[str]
    applicable_entities: list[str]  # slugs from detail-page taxonomy
    pdf_url_en: str | None
    pdf_url_fr: str | None
    published_at: date
    updated_at: date | None
    description: str             # scope text on the detail page
```

- `list_circulars(entity_slug: str, *, max_pages: int | None = None) -> Iterator[CirculatListingRow]` paginates `/en/regulatory-framework/?fwp_entity_type={slug}&fwp_content_type=circulars-cssf&paged=N`.
- `fetch_circular_detail(url: str) -> CircularDetail` fetches and parses one detail page.
- 500ms delay between requests; `httpx.Client(headers={"User-Agent": "RegulatoryWatcher/1.0"})`; one retry on 5xx; 404 on a detail URL raises `CircularNotFoundError`.

### Entity-slug mapping

```python
_CSSF_ENTITY_SLUGS: dict[AuthorizationType, str] = {
    AuthorizationType.AIFM: "aifms",
    AuthorizationType.CHAPTER15_MANCO: "management-companies-chapter-15",
}
```

Slugs verified at first run by an integration test that hits the real site and asserts at least one row is returned.

### Service: `regwatch/services/cssf_discovery.py`

```python
class CssfDiscoveryService:
    def run(
        self,
        *,
        entity_types: list[AuthorizationType],
        mode: Literal["full", "incremental"],
        triggered_by: str,
    ) -> int:  # returns run_id
```

Creates a `DiscoveryRun` row. For each entity_type, iterates listings per the mode semantics:
- **incremental** — stops that entity's walk at the first listing row whose `reference_number` already exists in the DB.
- **full** — walks every page.

Per discovered listing row:
1. Fetch detail (`fetch_circular_detail`).
2. Reconcile against DB (see below).
3. Write a `DiscoveryRunItem` row with the outcome.

### Reconciliation rules

Inputs: `CircularDetail` + existing `Regulation` (if any) + existing `RegulationOverride` rows.

| Situation | Action | Outcome |
|---|---|---|
| No regulation row exists | Insert `Regulation` with `source_of_truth="CSSF_WEB"`. Apply ICT heuristic. Write `RegulationApplicability` for each queried entity type. Insert `RegulationLifecycleLink` rows for parsed amendment relationships (create stubs for unknown refs). Store `url=pdf_url_en or pdf_url_fr`. | `NEW` |
| Row exists; amendment graph differs | Insert missing `RegulationLifecycleLink` rows. Update `url` if the detail page has a newer PDF. | `AMENDED` |
| Row exists; detail `updated_at` is newer than our last refresh, but amendment graph is unchanged | Refresh scraped metadata (title, description, applicable_entities). | `UPDATED_METADATA` |
| Row exists; nothing changed | No write. | `UNCHANGED` |
| Detail URL returns 404 for a previously-discovered reference | Mark `lifecycle_stage="REPEALED"` (do NOT delete). | `WITHDRAWN` |

**Override precedence:** `RegulationOverride.action == "EXCLUDE"` → skip entirely, outcome `UNCHANGED` with note "excluded by override". `SET_ICT` / `UNSET_ICT` always win over the ICT heuristic.

### ICT heuristic

Combine the detail page's title + description; lowercase; substring-match against:

```
{"ict", "information security", "cybersecurity", "cyber-security",
 "operational resilience", "dora", "outsourcing", "third party risk",
 "third-party risk", "it governance", "cloud", "business continuity",
 "nis2", "security risk management"}
```

Match → `is_ict=True, needs_review=False`. No match → `is_ict=None, needs_review=True` (existing `classify_catalog` LLM pass will handle it later).

### Amendment graph

Use the existing `RegulationLifecycleLink` table (already in `regwatch/db/models.py`; relation values: `AMENDS`, `REPEALS`, `SUCCEEDS`, `TRANSPOSES`, `PROPOSAL_OF`).

For each parsed amendment relationship where the referenced regulation doesn't yet exist, create a stub `Regulation` row (`title=reference_number`, `source_of_truth="CSSF_STUB"`, `needs_review=True`, `url=""`). Subsequent discovery runs will enrich these stubs when the referenced circular is scraped directly.

### New tables

```python
class DiscoveryRun(Base):
    __tablename__ = "discovery_run"
    run_id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str]                        # RUNNING | SUCCESS | PARTIAL | FAILED
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    triggered_by: Mapped[str]                  # USER_UI | USER_CLI | SCHEDULER
    entity_types: Mapped[list[str]] = mapped_column(JSON)
    mode: Mapped[str]                          # full | incremental
    total_scraped: Mapped[int] = 0
    new_count: Mapped[int] = 0
    amended_count: Mapped[int] = 0
    updated_count: Mapped[int] = 0
    unchanged_count: Mapped[int] = 0
    withdrawn_count: Mapped[int] = 0
    failed_count: Mapped[int] = 0
    error_summary: Mapped[str | None]

    items: Mapped[list[DiscoveryRunItem]] = relationship(...)


class DiscoveryRunItem(Base):
    __tablename__ = "discovery_run_item"
    item_id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = FK → discovery_run
    regulation_id: Mapped[int | None] = FK → regulation  # null for FAILED/WITHDRAWN stubs
    reference_number: Mapped[str]
    outcome: Mapped[str]                       # NEW / AMENDED / UPDATED_METADATA / UNCHANGED / WITHDRAWN / FAILED
    detail_url: Mapped[str | None]
    entity_types: Mapped[list[str]] = mapped_column(JSON)  # which run-slug(s) surfaced this row
    note: Mapped[str | None]                   # e.g. error detail, override-excluded, etc.
    created_at: Mapped[datetime]
```

Both land via the existing `sync_schema` additive migration at app startup.

### Web surface

- **`POST /catalog/discover-cssf`** — form params: `mode` (defaults `incremental`), `entity_types[]` (defaults all configured auth types). Creates run row, spawns daemon thread, redirects `303 → /discovery/runs/{id}`.
- **`GET /discovery/runs/{id}`** — HTMX-polled progress page (mirrors `/analysis/runs/{id}`).
- **`GET /discovery/runs/{id}/status`** — fragment that also contains the diff summary when complete.
- **`GET /discovery/runs`** — list of past runs with counts.
- Button on `/catalog`: "Discover from CSSF" alongside the existing "Refresh catalog" LLM button.

### CLI

- `regwatch discover-cssf [--full] [--entity AIFM|CHAPTER15]` — defaults `incremental` + all configured types.
- Stores run; prints final diff summary.

### Background execution

Same threaded-worker pattern as the analysis runner. Progress tracked via a new `CssfDiscoveryProgress` dataclass (`app.state.cssf_discovery_progress`).

## Config additions

```yaml
cssf_discovery:
  base_url: "https://www.cssf.lu/en/regulatory-framework/"
  request_delay_ms: 500
  max_retries: 1
  user_agent: "RegulatoryWatcher/1.0"
  content_types: ["circulars-cssf"]   # extension point for future: regulation-cssf etc.
```

Defaults live in `AnalysisConfig`'s sibling `CssfDiscoveryConfig` pydantic model.

## Failure modes

| Scenario | Behaviour |
|---|---|
| Site unreachable | Mark run as `FAILED`, `error_summary` captures the httpx exception. |
| Single detail page fetch 5xx (after retry) | `DiscoveryRunItem.outcome="FAILED"`, continue the run. |
| Detail page HTML structure changed → parser raises | `FAILED` item, continue. Run ends `PARTIAL`. |
| Site filter slug is wrong → empty listings | Run ends `SUCCESS` with 0 rows processed. The integration test against the live site catches this early. |
| sqlite-vec locked | `busy_timeout=10000` handles contention with concurrent analyses. |

## Testing

- Unit: listing-row parser, detail-page parser, amendment-ref parser, ICT heuristic, reconciliation branches — all driven by HTML fixtures in `tests/fixtures/cssf/`.
- Integration: run the scraper against a local mock HTTP server built on `httpx.MockTransport`.
- **One live test**, marked `@pytest.mark.live`, excluded from default `pytest`: hits the real CSSF site, asserts at least one result per entity slug. Run manually when upgrading scraper.

## Non-goals revisited

- No caching layer. A 500ms delay plus incremental mode keeps the real request volume low.
- No PDF download / text extraction in this flow. The existing analysis pipeline does that on demand (user clicks "Analyse").
- No change-detection on the amendment graph other than inserting new `RegulationLifecycleLink` rows — we never delete links.
