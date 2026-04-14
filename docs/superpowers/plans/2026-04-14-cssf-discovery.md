# CSSF Discovery — Implementation Plan

> **Light review cadence:** Per-task TDD implementer subagent + spec review. Skip the code-quality review between tasks; run one final full review at the end.

**Goal:** Authoritative CSSF website scraping with amendment tracking and per-run diff reporting.

**Architecture:** Pure scraper → service → threaded worker. New tables `discovery_run` / `discovery_run_item`. HTML fixtures drive the parser tests; one `live` test confirms slug correctness against the real site.

**Spec:** `docs/superpowers/specs/2026-04-14-cssf-discovery-design.md`

---

## Task 1 — `DiscoveryRun` + `DiscoveryRunItem` models

**Files:**
- Modify: `regwatch/db/models.py` (append)
- Test: `tests/unit/test_discovery_models.py`

Test asserts round-trip insert + relationship traversal. Follows the existing pattern from `AnalysisRun` / `DocumentAnalysis`.

Model field list is in spec section "New tables".

Commit: `feat(db): add DiscoveryRun and DiscoveryRunItem models`

## Task 2 — `regwatch/discovery/cssf_scraper.py`

**Files:**
- Create: `regwatch/discovery/__init__.py` (empty)
- Create: `regwatch/discovery/cssf_scraper.py`
- Create: `tests/unit/test_cssf_scraper.py`
- Create: `tests/fixtures/cssf/listing_page_1.html` — a trimmed real response body (save one from a live fetch during implementation)
- Create: `tests/fixtures/cssf/detail_22_806.html` — the detail page for CSSF 22/806

**Tests:**
- Listing parser yields N rows with expected fields.
- Pagination stops when a page contains zero rows.
- Detail parser correctly splits `"Circular CSSF 22/806 (as amended by Circular CSSF 25/883) on outsourcing"` → `reference_number="CSSF 22/806"`, `clean_title="on outsourcing"`, `amended_by_refs=["CSSF 25/883"]`.
- Detail parser extracts `applicable_entities`, `pdf_url_en`, `pdf_url_fr`, `published_at`, `updated_at`.
- 404 raises `CircularNotFoundError`.

All tests use `httpx.MockTransport` with the fixture HTML files — no live network.

Commit: `feat(discovery): CSSF listing + detail page scraper`

## Task 3 — ICT heuristic + amendment-ref parser

**Files:**
- Modify: `regwatch/discovery/cssf_scraper.py` (add helper functions) OR
- Create: `regwatch/discovery/heuristics.py`

Helpers:
- `is_ict_by_heuristic(title: str, description: str) -> bool` — lowercase substring match against the keyword set in spec.
- `parse_amendment_refs_from_title(title: str) -> list[str]` — extracts `CSSF NN/NNN` patterns from parentheticals like `(as amended by ...)`, `(repealing ...)`.
- `parse_amendment_refs_from_related(html: str) -> dict[str, list[str]]` — returns `{"AMENDS": [...], "REPEALS": [...], "SUCCEEDS": [...]}`.

Tests for each, HTML fixtures as needed.

Commit: `feat(discovery): ICT heuristic + amendment-ref parsers`

## Task 4 — Config addition + `CssfDiscoveryProgress`

**Files:**
- Modify: `regwatch/config.py` — add `CssfDiscoveryConfig`, attach to `AppConfig`.
- Modify: `config.example.yaml` — add `cssf_discovery:` block.
- Create: `regwatch/discovery/progress.py` — dataclass mirrors `AnalysisProgress`.
- Modify: `regwatch/main.py` — `app.state.cssf_discovery_progress = CssfDiscoveryProgress()`.

Spec in spec section "Config additions".

Commit: `feat(config): CSSF discovery config + progress state`

## Task 5 — `CssfDiscoveryService.run()`

**Files:**
- Create: `regwatch/services/cssf_discovery.py`
- Create: `tests/integration/test_cssf_discovery_service.py`

**Service shape:**

```python
class CssfDiscoveryService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        http_client: httpx.Client | None = None,
        config: CssfDiscoveryConfig,
        on_progress: Callable[[int, int, str], None] | None = None,
    ):
        ...

    def run(
        self,
        *,
        entity_types: list[AuthorizationType],
        mode: Literal["full", "incremental"],
        triggered_by: str,
    ) -> int:
        """Returns the run_id."""
```

**Tests (using `httpx.MockTransport` to inject fixture responses):**
- Empty DB + fresh crawl → all rows `NEW`, counts match.
- Re-run in incremental mode after first run → all rows `UNCHANGED`, walk stops at first known ref per entity.
- Re-run after the fixture is updated to add an `amended_by` to 22/806 → that item's outcome is `AMENDED` and a new `RegulationLifecycleLink` exists.
- Detail fetch raises `CircularNotFoundError` on a known ref → item `WITHDRAWN`, regulation `lifecycle_stage="REPEALED"`.
- `RegulationOverride(action="EXCLUDE")` → outcome `UNCHANGED` with note, no regulation written.
- Unknown amendment target → stub regulation created with `source_of_truth="CSSF_STUB"`, `needs_review=True`.

Commit: `feat(discovery): CssfDiscoveryService with amendment graph + diff outcomes`

## Task 6 — Web routes + templates

**Files:**
- Create: `regwatch/web/routes/discovery.py`
- Modify: `regwatch/web/routes/catalog.py` — add "Discover from CSSF" button form posting to `/catalog/discover-cssf`.
- Create: `regwatch/web/templates/discovery/run.html`
- Create: `regwatch/web/templates/discovery/_run_status.html`
- Create: `regwatch/web/templates/discovery/list.html`
- Modify: `regwatch/main.py` — register new router.
- Test: `tests/integration/test_discovery_routes.py`

**Routes:**
- `POST /catalog/discover-cssf` → create run, spawn daemon thread (pattern identical to `/catalog/analyse`), redirect to `/discovery/runs/{id}`.
- `GET /discovery/runs/{id}` → run page (HTMX-polled).
- `GET /discovery/runs/{id}/status` → status fragment.
- `GET /discovery/runs` → history list.

**Run page shows (when complete):**
- Status badge + counts per outcome.
- Table filterable by outcome, columns: reference, outcome, entity_types surfaced in, link to regulation detail (for non-FAILED), note.

Commit: `feat(web): /catalog/discover-cssf + run progress + history pages`

## Task 7 — CLI `regwatch discover-cssf`

**Files:**
- Modify: `regwatch/cli.py`
- Test: `tests/integration/test_cli_discover_cssf.py`

Flags: `--full` (default incremental), `--entity AIFM|CHAPTER15_MANCO` (repeatable; default = all configured auth types), `--triggered-by` internal.

Uses `httpx.MockTransport` in the test. Prints the per-outcome counts + returns exit code 0 / 1 / 2 following the pattern of the other CLI commands.

Commit: `feat(cli): regwatch discover-cssf`

## Task 8 — Live probe test + documentation

**Files:**
- Create: `tests/live/test_cssf_live_probe.py` — `@pytest.mark.live`; exercises one page of each entity slug against the real site; asserts ≥1 listing row. Excluded from default `pytest` run.
- Create: `tests/fixtures/cssf/README.md` — how to refresh the HTML fixtures from live pages.
- Modify: `CLAUDE.md` — append a section about the CSSF discovery invariants (scraper fixtures under `tests/fixtures/cssf/`, live test only runs with `-m live`).

Commit: `test(discovery): live slug-verification probe + fixture refresh docs`

---

## Final steps

After all 8 tasks:

1. Run full test suite — expect 292 + ~20 new tests passing.
2. Dispatch one final code-quality review over the whole `ca4ba4e..HEAD` delta.
3. If clean: smoke-test against live CSSF site via `regwatch discover-cssf --full`.
4. Report final catalog state + commit count.

---

## Task-agent contract notes

- Implementer subagents only get spec review, not code quality review, per user's "light approach" directive.
- Each commit follows the repo's existing TDD-per-task convention: failing test → implementation → passing test → commit.
- Work stays on `main`.
- Reuse existing patterns: `AnalysisRunner` + `AnalysisProgress` are the templates for the new discovery runner + progress dataclass.
- Tests use `httpx.MockTransport` for HTTP mocking (matches the project's existing `pytest-httpx` pattern from the fetch pipeline tests).
