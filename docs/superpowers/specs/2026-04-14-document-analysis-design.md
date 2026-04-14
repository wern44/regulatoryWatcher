# Document Analysis & Version-Scoped Chat — Design

**Date:** 2026-04-14
**Status:** Draft — approved by user, awaiting implementation plan

## Overview

An extension to the Regulatory Watcher that lets the user **manually select regulations** from the catalog, **download the authoritative documents** (or upload them when unavailable), and run **structured LLM extraction** against a **user-configurable field schema**. Extracted results persist per `DocumentVersion`, write back canonical fields to the parent `Regulation` (respecting existing overrides), and make documents **chat-able at version granularity**.

Four user-visible outcomes:

1. **Catalog → Analyse** action (single + multi-select) queues a background `AnalysisRun` over the selected regulations' current versions.
2. **Settings → Extraction Fields** page where the user edits custom fields added to every future analysis.
3. **Analysis view** on the regulation detail page showing each version's structured report and re-run history.
4. **Version-scoped chat** — the chat page gets a scope picker; the user picks any subset of `DocumentVersion`s (default: all).

## Architectural principles

- **Reuse the existing pipeline.** Fetch + extract phases are unchanged. Analysis is a new downstream step operating on `DocumentVersion.pdf_extracted_text` / `html_text`.
- **No new services.** Stay on sqlite-vec + FTS5 + LM Studio. Background execution reuses the existing threaded-worker + `PipelineProgress` pattern.
- **Field schema in the database.** Extraction fields are DB-backed rows editable from the UI, seeded with non-deletable core fields.
- **Analysis is re-runnable and versioned.** Analyses belong to `(version_id, run_id)`; the parent regulation gets the latest run's write-back.

The research input (Qdrant / BGE-M3 / bge-reranker / Haystack / LangGraph / Graph RAG / HyDE / CRAG / HalluGraph) was evaluated against the existing stack and the single-user local scale. We adopt **structure-aware chunking with metadata injection** (the highest-leverage idea from the research) and explicitly defer everything else. sqlite-vec + FTS5 is sufficient for the corpus scale and preserves the one-SQLite-file deployment property. Cross-encoder reranking, Graph RAG, HyDE, CRAG, and HalluGraph remain candidates for a future phase once we have measured retrieval quality against a golden test set.

## Data model

Three new tables, one additive column on `regulation`. All changes land via the existing `sync_schema` helper — no Alembic needed.

### `extraction_field` — the field registry

| Column | Type | Notes |
|---|---|---|
| `field_id` | int PK | |
| `name` | str unique | machine name, e.g. `main_points` |
| `label` | str | display name, e.g. "Main Points" |
| `description` | text | doubles as the LLM extraction prompt for this field |
| `data_type` | enum | `TEXT`, `LONG_TEXT`, `BOOL`, `DATE`, `ENUM`, `LIST_TEXT` |
| `enum_values` | JSON nullable | populated when `data_type = ENUM` |
| `is_core` | bool | non-deletable built-ins; user can toggle `is_active` but not remove |
| `is_active` | bool | excluded from future analyses when false |
| `canonical_field` | str nullable | for core fields that write back to `regulation` (`is_ict`, `transposition_deadline`, `applicable_entity_types`, `replaced_by_id`) |
| `display_order` | int | |
| `created_at` | datetime | |

Core rows seeded at `init-db`:

| name | data_type | canonical_field |
|---|---|---|
| `main_points` | `LONG_TEXT` | — |
| `scope_description` | `LONG_TEXT` | — |
| `applicable_entity_types` | `LIST_TEXT` | `applicable_entity_types` |
| `is_ict` | `BOOL` | `is_ict` |
| `ict_reasoning` | `LONG_TEXT` | — |
| `is_relevant_to_managed_entities` | `BOOL` | — |
| `relevance_reasoning` | `LONG_TEXT` | — |
| `implementation_deadline` | `DATE` | conditional — `transposition_deadline` or `application_date` based on `document_relationship` |
| `deadline_description` | `TEXT` | — |
| `document_relationship` | `ENUM` (`NEW`, `REPLACES`, `AMENDS`, `REPEALS`, `CLARIFIES`) | — |
| `relationship_target` | `TEXT` | drives `replaced_by_id` when relationship = `REPLACES` |
| `keywords` | `LIST_TEXT` | — |

### `analysis_run` — sibling of `PipelineRun`

| Column | Type | Notes |
|---|---|---|
| `run_id` | int PK | |
| `status` | enum | `PENDING`, `RUNNING`, `SUCCESS`, `PARTIAL`, `FAILED` |
| `queued_version_ids` | JSON | versions selected at queue time |
| `started_at` | datetime nullable | |
| `finished_at` | datetime nullable | |
| `llm_model` | str | captured at run start for audit |
| `triggered_by` | str | `USER_UI`, `USER_CLI` |
| `error_summary` | text nullable | aggregated failure detail |

### `document_analysis` — one row per `(version_id, run_id)`

| Column | Type | Notes |
|---|---|---|
| `analysis_id` | int PK | |
| `run_id` | FK → `analysis_run` | |
| `version_id` | FK → `document_version` | |
| `regulation_id` | FK → `regulation` | denormalized for fast latest-analysis lookup |
| `status` | enum | `SUCCESS`, `FAILED` |
| `error_detail` | text nullable | |
| `raw_llm_output` | text | full JSON from the LLM, preserved for re-parsing and debugging |
| `was_truncated` | bool | document text was cut to fit the context window |
| `main_points` | text nullable | |
| `scope_description` | text nullable | |
| `applicable_entity_types` | JSON nullable | |
| `is_ict` | bool nullable | |
| `ict_reasoning` | text nullable | |
| `is_relevant_to_managed_entities` | bool nullable | |
| `relevance_reasoning` | text nullable | |
| `implementation_deadline` | date nullable | |
| `deadline_description` | text nullable | |
| `document_relationship` | str nullable | one of the enum values listed above |
| `relationship_target` | str nullable | free-text reference, e.g. `"CSSF 12/552"` |
| `keywords` | JSON nullable | |
| `custom_fields` | JSON | `{field_name: value}` for every non-core active `extraction_field` |
| `llm_confidence` | float nullable | |
| `token_usage` | JSON nullable | |
| `created_at` | datetime | |

Indexes: `uq(version_id, run_id)`, `ix(regulation_id, created_at desc)`.

### Additive column on `regulation`

| Column | Type | Notes |
|---|---|---|
| `applicable_entity_types` | JSON nullable | mirrors the existing column on `update_event`; populated by write-back |

### Additive column on `document_chunk`

| Column | Type | Notes |
|---|---|---|
| `heading_path` | JSON nullable | from structure-aware chunking — e.g. `["Chapter III", "Article 17", "§2"]` |

## Write-back contract

A single function, `regwatch/analysis/writeback.py::apply_writeback(session, analysis)`, executed in the same transaction as the `document_analysis` insert.

Rules, in order:

1. **`is_ict`** writes back **unless** a `RegulationOverride` of type `SET_ICT` / `UNSET_ICT` / `EXCLUDE` exists for this regulation (matches the existing precedence in `DiscoveryService.classify_catalog`).
2. **`implementation_deadline`** writes to:
   - `regulation.transposition_deadline` when `document_relationship ∈ {REPLACES, AMENDS}` and the regulation type is an EU directive (has `celex_id`)
   - `regulation.application_date` otherwise
3. **`document_relationship = REPLACES`** with a resolvable `relationship_target` (lookup by `reference_number` or `celex_id`) updates `regulation.replaced_by_id`. Unresolved targets log a warning; no write.
4. **`applicable_entity_types`** writes to the new column on `regulation`.
5. **`main_points`, `scope_description`, `keywords`** stay on `document_analysis` only — they are per-version content, not canonical regulation state.

Only the **latest** analysis of the **current version** triggers write-back. Re-analysing an older version never mutates the parent regulation.

## Analysis pipeline

New package: `regwatch/analysis/` (sibling of `rag/`, not a phase inside the ingest pipeline).

```
regwatch/analysis/
  __init__.py
  fields.py          # load active extraction_field rows → prompt schema
  extractor.py       # LLM call + JSON parsing → structured result
  writeback.py       # apply canonical fields to Regulation
  runner.py          # AnalysisRun orchestrator, threaded, updates PipelineProgress

regwatch/services/
  analysis.py        # service DTOs + queue_run() / get_run() / list_analyses()
```

### Entry points

- **Web:** `POST /catalog/analyse` (form with `regulation_ids[]`) → creates `analysis_run`, spawns worker thread, redirects to `/analysis/runs/{run_id}`.
- **CLI:** `regwatch analyse --reg REF [--reg REF ...]` and `regwatch analyse --all-ict`. Blocks until done; prints a compact summary.

### Per-document execution (inside the worker thread)

1. **Resolve the document.** Find the current `DocumentVersion` for the regulation. If none:
   - If a registered source plugin matches `regulation.source_of_truth`, invoke it for this regulation.
   - Otherwise fall back to a generic HTTP fetch of `regulation.url` through the existing `extract/html.py` + `extract/pdf.py`.
   - If still no text, mark the document's analysis `FAILED` with a clear message; continue the run.
2. **Build the prompt** (`fields.py::build_prompt_schema`):
   - Load all `is_active=True` rows from `extraction_field` ordered by `display_order`.
   - Render a JSON-schema description into the user message, one line per field with name, type hint, and description (the user-edited prompt text).
   - Prepend regulation metadata: `Regulation: {reference_number} — {title} — {issuing_authority}`.
   - Append document text truncated with a tail-biased budget (`config.analysis.max_document_tokens`); set `was_truncated` when cut.
3. **Call the LLM once** (`extractor.py::extract`). Temperature `0`. Parse JSON; validate each field against its `data_type`; coerce (dates → `date`, bools → bool, enum → str, `LIST_TEXT` → list of strings). On parse failure, save `raw_llm_output`, mark the analysis `FAILED`. No retries in v1 — re-run is the retry unit.
4. **Persist** `document_analysis`; apply write-back in the same session; single commit. Update `PipelineProgress` ("analysed 3 of 7"). Log token usage.
5. **Refresh chunk denormalization.** If `is_ict` changed on write-back, `UPDATE document_chunk SET is_ict=? WHERE regulation_id=?` for this regulation. No re-embedding.

### Run completion

- `analysis_run.status = SUCCESS` if all docs succeeded.
- `PARTIAL` if some succeeded and some failed; `error_summary` aggregates per-doc failures.
- `FAILED` if all failed.

### Not built in v1

- No auto-analysis on new-version arrival. Catalog shows a "needs re-analysis" badge; user clicks Analyse.
- No map-reduce over document chunks. Truncate-to-fit with a `was_truncated` flag; add map-reduce in a follow-up if users hit truncation often.
- No LLM retries.

## Manual upload

When a regulation has no fetchable document (seeded-but-never-fetched, source-not-supported, last fetch failed, or paywalled) the user uploads the PDF/HTML directly.

### Entry points

- **Catalog row** with no current version or failed last fetch → "Upload document" button next to "Analyse".
- **Regulation detail page** → always-available "Upload new version".
- **Multi-select batch** → the batch confirmation page lists regulations missing a version with per-row upload widgets.

### Backend flow — `POST /catalog/{regulation_id}/upload` (multipart)

1. Stream the file to disk under `{paths.pdf_dir}/{regulation_reference}/{uuid}.pdf`.
2. Extract text via `regwatch/pipeline/extract/pdf.py` — the same extractor the fetch pipeline uses.
3. Hash text; if it matches `content_hash` of any existing `DocumentVersion` for this regulation, short-circuit ("already have this content"); link to the existing version. Same idempotency contract as the persist phase.
4. Otherwise create a new `DocumentVersion` with `pdf_manual_upload=True`, `source_url="manual-upload"`, incremented `version_number`, prior version's `is_current=False`. Compute `change_summary` via `pipeline/diff.py`.
5. Index chunks via `rag/indexing.py`.
6. Redirect to regulation detail with flash "Uploaded; analyse now?" + one-click queue button.

### Validation & safety

- Accept `.pdf`, `.html`, `.htm` only. MIME sniff + extension must agree.
- Max size: `config.analysis.max_upload_size_mb` (default 25 MB). Rejected files get no DB row and are deleted.
- Protected/encrypted PDFs: the extractor already detects this and sets `pdf_is_protected`. The version is created for archival; chat + analysis are disabled for it with a visible reason.

### CLI parity

`regwatch upload --reg REF path/to/file.pdf [--analyse]`.

## Structure-aware chunking

Rewrite `regwatch/rag/chunker.py` to split on legal-document boundaries first; fall back to the existing recursive splitter only when a section exceeds the token budget.

### New `Chunk` shape

```python
@dataclass
class Chunk:
    index: int
    text: str              # original content — stored in DocumentChunk.text, indexed in FTS
    embed_text: str        # metadata-prefixed — what the embedder sees
    token_count: int
    heading_path: list[str]  # e.g. ["Chapter III", "Article 17", "§2"]
```

### Splitting strategy

1. Detect language once at the top (move the existing `langdetect` call from `indexing.py` into the chunker so it can select patterns).
2. **Pass 1 — structural boundaries.** Regex patterns per language, tried in hierarchy order:
   - Level 0: `Chapter`/`Chapitre`/`Kapitel` + numeral
   - Level 1: `Article`/`Artikel`/`§` + number (with optional sub-letter)
   - Level 2: numbered list items at start of line, `\(\d+\)`, `Absatz`, `alinéa`
   - CSSF-specific: `\d+(\.\d+)*\s+[A-Z]` (e.g. `"1.2.3 Risk management"`)
3. Walk the document, maintain a running `heading_path`. Emit one chunk per Article (primary retrieval unit) with its path.
4. **Pass 2 — size enforcement.** When an article exceeds `chunk_size_tokens`, fall back to `RecursiveCharacterTextSplitter` *within* that article, preserving `heading_path` on every sub-chunk. Never split across article boundaries.
5. **Fallback** — if Pass 1 finds zero structural matches, the current recursive splitter runs with today's behaviour. No regression for non-legal content.

### Metadata injection into `embed_text`

```
[CSSF Circular 12/552 | Chapter III | Article 17 | Luxembourg | 2012]

{original chunk text}
```

Built from `(regulation.reference_number, heading_path, issuing_authority, publication_date.year)`.

### Indexing changes (`rag/indexing.py`)

- Embed `chunk.embed_text` (metadata-enriched).
- Store `chunk.text` (original) in `DocumentChunk.text` and `document_chunk_fts` — citations show original paragraphs; FTS queries don't match against injected headers.
- Persist `heading_path` as a JSON column on `DocumentChunk`.

### Re-index policy

Structure-aware chunking breaks existing chunks. Existing content keeps working with its current chunks; no forced re-index. A new CLI command `regwatch reindex [--regulation REF | --all]` triggers re-chunk + re-embed. Documented as a one-time upgrade step.

## Version-scoped chat

### Retriever (`regwatch/rag/retrieval.py`)

```python
@dataclass
class RetrievalFilters:
    ...                                     # existing fields unchanged
    version_ids: list[int] = field(default_factory=list)
```

- `_hydrate` adds one branch: `if filters.version_ids and r.version_id not in filters.version_ids: continue`.
- When `version_ids` is non-empty, pool size doubles (`pool = max(top_k * 6, 60)`) to compensate for aggressive post-filtering.
- Filtering stays client-side — consistent with the pool+hydrate rationale in `CLAUDE.md`.

### Chat UI

- A **scope bar** above the message input. Default chip: "Scope: all documents (N)". Click opens a modal.
- Modal: tree of Regulations grouped by `is_ict` / authority, each expandable to its `DocumentVersion`s with `fetched_at` and `is_current` markers. Checkboxes at both levels — checking a regulation auto-checks its current version.
- Selected scope surfaces as dismissable chips. Empty selection = all documents.
- Scope persists in browser **`sessionStorage`** — survives page reloads, scoped to the tab, no new DB table. Sent as `version_ids[]` in the POST.

### Edge cases

- Requested versions with no chunks → banner: "N versions aren't indexed. Analyse them to enable chat." with an Analyse button.
- `pdf_is_protected` versions → shown in the modal with a warning icon, excluded from the count.
- Mix of indexed and non-indexed → chat works on indexed; non-indexed listed as skipped.

### Citation rendering

Citations show `regulation.reference_number` + `heading_path` — e.g. "CSSF 12/552, Article 17, §2" — instead of today's naked snippet. Click expands the full chunk. Links go to `/regulations/{id}/versions/{version_id}#chunk-{chunk_id}`.

### CLI parity

- `regwatch chat "question" --version 42 --version 43`
- `regwatch chat "question" --reg CSSF-12/552` (regulation flag expands to the current version)

## UI changes summary

### New: `/settings/extraction` — Extraction Field Manager

- Table of `extraction_field` rows (order, name, label, type, active?, core?).
- Inline edit for user-added fields; core rows have immutable `name`, `data_type`, `canonical_field` but editable `description` and `is_active`.
- "Add field" modal. Reorder via drag handles.
- Description field is a multi-line textarea with a live preview of its rendering inside the LLM prompt.

### Updated: `/catalog`

- Checkbox column; header checkbox for select-all-on-page.
- Sticky action bar on selection: "Analyse (N)", "Download current (N)", "Clear selection".
- Row actions menu: "Analyse", "Upload document", "View latest analysis".
- New "Analysis status" column: `—` / `✓ 2026-04-10` / `⚠ needs re-analysis` / `FAILED`.

### Updated: `/regulations/{id}`

- New "Analysis" tab.
- Latest `document_analysis` per `DocumentVersion`: core fields two-column, custom fields grouped, timestamp + model + truncation flag.
- Raw JSON expandable.
- "History" dropdown lists prior analyses for cross-run comparison.
- "Upload new version" button top-right.
- "Re-analyse this version" per-version.

### New: `/analysis/runs/{run_id}` — Run progress

- Mirrors `/pipeline/runs/{run_id}`. HTMX polls every 2s until terminal state.
- Completion view: N analysed, M failed, per-document outcomes with links.

### Updated: `/chat`

- Scope bar + modal. Otherwise unchanged.

### Not changed

Inbox, Deadlines, ICT, Drafts — they read from `Regulation` / `UpdateEvent`; the write-back keeps them accurate automatically.

## Config additions

```yaml
analysis:
  llm_call_timeout_seconds: 120
  max_document_tokens: 24000
  max_upload_size_mb: 25
  pdf_dir: data/pdfs
```

No new config for the LLM itself — extraction uses the same `LLMClient` as chat and discovery.

## Testing

Follows the repo's existing conventions: integration uses a real SQLite in `tmp_path`; mocks only LLM and outbound HTTP.

### Unit tests (`tests/unit/`)

- `test_extraction_fields.py` — prompt schema generation, type coercion, enum validation.
- `test_structure_aware_chunker.py` — EN/FR/DE article detection, heading_path, fallback to recursive splitter, no cross-article splits, metadata-injection in `embed_text` only.
- `test_analysis_writeback.py` — override precedence, deadline routing (`transposition_deadline` vs `application_date`), `REPLACES` resolution.
- `test_retrieval_filters.py` — `version_ids` filter narrows hydration, empty list = unfiltered, doubled pool.

### Integration tests (`tests/integration/`)

- `test_analysis_run.py` — queue a run; mocked LLM returns fixed JSON; assert row + write-back + `PipelineProgress` transitions.
- `test_upload_route.py` — multipart POST with a small PDF fixture; version created, chunks indexed, dedup on second upload.
- `test_chat_scope.py` — index two versions, scope to one, assert retrieval excludes the other.
- `test_run_failure_partial.py` — three queued; middle raises; run ends `PARTIAL` with per-doc statuses correct.

### Performance target

Full suite under 15s (existing ~6s + additions).

## Phases

Each phase is independently mergeable; the tool is usable at every boundary.

1. **Phase A — schema + field registry.** New tables, `sync_schema` migration, seeded core rows, `/settings/extraction` UI.
2. **Phase B — analysis engine.** `regwatch/analysis/` package, service layer, CLI command, tests. No UI surface yet — CLI-only.
3. **Phase C — UI integration.** Catalog multi-select + Analyse, regulation detail Analysis tab, run progress page, upload routes.
4. **Phase D — structure-aware chunking + `reindex` CLI.** Upgrade note documenting a one-time re-index.
5. **Phase E — version-scoped chat.** Filter addition, scope bar, citation rendering with `heading_path`.

Phases D and E can swap order if chat-scope is higher priority than chunk-quality improvement.

## Non-goals

- Qdrant / other vector DB migration.
- Haystack / LangGraph / LangChain agent orchestration.
- Cross-encoder reranking, Graph RAG, HyDE, CRAG, HalluGraph — phase 3/4 material in the research, explicitly deferred.
- Fine-tuning of the embedding model or LLM.
- Automatic analysis on new-version arrival — manual only.
- Chat history persistence — chat remains stateless server-side.
- Multi-user / auth — tool remains single-user local.
