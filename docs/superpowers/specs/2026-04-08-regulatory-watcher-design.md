# Regulatory Watcher — Design

**Date:** 2026-04-08
**Author:** Brainstorming session
**Status:** Approved

## 1. Purpose & scope

A local single-user tool that continuously monitors the regulatory landscape for **Union Investment Luxembourg S.A.** (LEI `529900FSORICM1ERBP05`) and presents it in a structured browser UI.

The entity holds two CSSF authorisations that must be tracked separately:

- **AIFM** (Law of 12 July 2013)
- **Chapter 15 Management Company** (Law of 17 December 2010)

The tool ingests updates from CSSF, EUR-Lex, Legilux, ESMA, EBA and EC-FISMA, matches them against a curated catalog of ~50 in-force regulations, detects changes (with full version history and diffs), flags ICT/DORA items, and surfaces drafts and upcoming deadlines early. A local RAG layer (Ollama + sqlite-vec) lets the user ask questions about the indexed regulations.

Out of scope for the first version: multi-user support, authentication, email/desktop notifications, mobile UI, i18n.

## 2. Stack & architectural shape

- **Language:** Python 3.11+
- **Web framework:** FastAPI (uvicorn, single process)
- **Templates:** Jinja2 + HTMX + Tailwind CSS (CDN) + optional Alpine.js
- **ORM / migrations:** SQLAlchemy 2 + Alembic
- **Database:** SQLite with the `sqlite-vec` extension (one `app.db` file) and FTS5 for sparse retrieval
- **Scheduler:** APScheduler (in-process, started in FastAPI lifespan)
- **Local LLM:** Ollama on `localhost:11434`, chat model `llama3.1:8b` (default), embedding model `nomic-embed-text`
- **CLI:** Typer
- **Testing:** pytest + pytest-asyncio + pytest-httpx
- **UI language:** English

The ingestion side is built as a **five-phase pipeline** — Fetch → Extract → Match → Persist → Notify — with a stable interface between phases. Each phase is a module with a single responsibility. Sources are plugins; extract, match, persist, and notify are source-agnostic. Ollama is called from exactly two places: the Match phase (for cross-reference extraction and semantic fallback) and the RAG service (for Q&A).

### Runtime process layout

```
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI App (uvicorn)                     │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐   │
│  │   Web UI     │   │   REST API   │   │   RAG / Q&A    │   │
│  │  Jinja2 +    │◄──┤  /regulations│◄──┤  retrieval +   │   │
│  │  HTMX + TW   │   │  /updates    │   │  Ollama chat   │   │
│  └──────┬───────┘   │  /inbox      │   └────────┬───────┘   │
│         │           │  /chat       │            │           │
│         │           └──────┬───────┘            │           │
│         │                  │                    │           │
│  ┌──────▼──────────────────▼────────────────────▼───────┐   │
│  │             Service Layer (use-cases)                │   │
│  └─────────┬────────────────────────┬────────────────── ┘   │
│            │                        │                       │
│  ┌─────────▼────────┐   ┌───────────▼──────────┐            │
│  │   APScheduler    │──►│   Pipeline Runner    │            │
│  └──────────────────┘   │  fetch→extract→match │            │
│                         │  →persist→notify     │            │
│                         └───────────┬──────────┘            │
│                                     │                       │
│  ┌──────────────────────────────────▼─────────────────────┐ │
│  │             SQLite (app.db) + sqlite-vec + FTS5        │ │
│  └──────────────────────────────────┬─────────────────────┘ │
└─────────────────────────────────────┼───────────────────────┘
                                      │
                    ┌─────────────────▼──────────────────┐
                    │   Ollama (localhost:11434)         │
                    │   - llama3.1 (chat + match)        │
                    │   - nomic-embed-text (embeddings)  │
                    └────────────────────────────────────┘
```

## 3. Data model

The schema lives in SQLite. All tables are defined in SQLAlchemy, migrations via Alembic.

### 3.1 Entity and authorisation

**`entity`** — one row, the monitored legal person.
Columns: `lei` (PK), `legal_name`, `rcs_number`, `address`, `jurisdiction`, `nace_code`, `gleif_last_updated`.

**`authorization`** — two rows (AIFM, Chapter 15 ManCo).
Columns: `authorization_id` (PK), `lei` (FK→entity), `type` (enum `AIFM` / `CHAPTER15_MANCO`), `cssf_entity_id`, `authorization_date`, `status`, `cssf_url`.

### 3.2 Regulation catalog

**`regulation`** — curated seed + auto-discovered additions.
Columns: `regulation_id` (PK), `type` (enum `LU_LAW` / `CSSF_CIRCULAR` / `CSSF_REGULATION` / `EU_REGULATION` / `EU_DIRECTIVE` / `ESMA_GUIDELINE` / `RTS` / `ITS` / `DELEGATED_ACT`), `reference_number`, `celex_id` (nullable), `eli_uri` (nullable), `title`, `issuing_authority`, `publication_date`, `effective_date`, `lifecycle_stage` (enum `CONSULTATION` / `PROPOSAL` / `DRAFT_BILL` / `ADOPTED_NOT_IN_FORCE` / `IN_FORCE` / `AMENDED` / `REPEALED`), `transposition_deadline` (date, nullable), `application_date` (date, nullable), `is_ict` (bool), `dora_pillar` (nullable enum), `url`, `source_of_truth` (enum `SEED` / `DISCOVERED`), `replaced_by_id` (FK→regulation, nullable), `notes`.

**`regulation_alias`** — regex and ID patterns for rule-based matching.
Columns: `alias_id` (PK), `regulation_id` (FK), `pattern`, `kind` (enum `EXACT` / `REGEX` / `CELEX` / `ELI`).

**`regulation_applicability`** — which authorisation(s) a regulation applies to.
Columns: `applicability_id` (PK), `regulation_id` (FK), `authorization_type` (enum `AIFM` / `CHAPTER15_MANCO` / `BOTH`), `scope_note` (nullable).

**`regulation_lifecycle_link`** — self-referencing M:N relationship between a draft / proposal / amending act and its target.
Columns: `link_id` (PK), `from_regulation_id` (FK), `to_regulation_id` (FK), `relation` (enum `PROPOSAL_OF` / `TRANSPOSES` / `AMENDS` / `REPEALS` / `SUCCEEDS`).

Examples:

- `52021PC0721 PROPOSAL_OF 32024L0927` (Commission proposal → adopted Directive)
- `LU Draft Bill 8628 TRANSPOSES 32024L0927` (LU draft → EU directive)
- `32024L0927 AMENDS 32011L0061` (AIFMD II → AIFMD I)

### 3.3 Document versions and updates

**`document_version`** — every version of a document is archived.
Columns: `version_id` (PK), `regulation_id` (FK), `version_number` (int, monotonic per regulation), `is_current` (bool, exactly one per regulation), `fetched_at`, `source_url`, `content_hash` (sha256), `html_text` (nullable), `pdf_path` (nullable, relative path into the archive), `pdf_extracted_text` (nullable), `pdf_is_protected` (bool), `pdf_manual_upload` (bool), `change_summary` (nullable, populated with `difflib.unified_diff` output for versions ≥ 2).

**`update_event`** — one event per new item from a source feed.
Columns: `event_id` (PK), `source` (enum `CSSF_RSS` / `EUR_LEX_SPARQL` / `LEGILUX_SPARQL` / `ESMA_RSS` / `EBA_RSS` / `EC_FISMA_RSS`), `source_url`, `title`, `published_at`, `fetched_at`, `raw_payload` (JSON), `content_hash`, `is_ict` (nullable bool), `severity` (enum `INFORMATIONAL` / `MATERIAL` / `CRITICAL`), `review_status` (enum `NEW` / `SEEN` / `ASSESSED` / `ARCHIVED`), `seen_at` (nullable), `notes`.

**`update_event_regulation_link`** — M:N between events and affected regulations.
Columns: `link_id` (PK), `event_id` (FK), `regulation_id` (FK), `match_method` (enum `REGEX_ALIAS` / `CELEX_ID` / `ELI_URI` / `OLLAMA_REFERENCE` / `OLLAMA_SEMANTIC` / `MANUAL`), `confidence` (float 0–1), `matched_snippet` (text).

**`pipeline_run`** — one row per pipeline execution (for audit and the UI log).
Columns: `run_id` (PK), `started_at`, `finished_at`, `status` (enum `RUNNING` / `COMPLETED` / `FAILED` / `ABORTED`), `sources_attempted` (JSON array), `sources_failed` (JSON array), `events_created` (int), `versions_created` (int), `error` (nullable).

### 3.4 RAG storage

**`document_chunk`** — chunked text for retrieval, one row per chunk per version.
Columns: `chunk_id` (PK), `version_id` (FK), `regulation_id` (FK, denormalised for filter speed), `chunk_index`, `text`, `token_count`, `language`, `lifecycle_stage` (denormalised), `is_ict` (denormalised), `authorization_types` (JSON).

**`document_chunk_vec`** — sqlite-vec virtual table.
Columns: `chunk_id` (rowid), `embedding` (float[768]).

**`document_chunk_fts`** — FTS5 virtual table on `text` for BM25 keyword retrieval.

**`chat_session`** + **`chat_message`** — persisted Q&A history with `retrieved_chunk_ids` on each assistant turn.

### 3.5 Design rationale for two orthogonal axes

`update_event` is the source of truth for *what is new*. `document_version` is the source of truth for *what currently applies*. One event may affect multiple regulations; one regulation may be changed by multiple events. The M:N link table reflects that cleanly and keeps diffs separate from feed items.

## 4. Ingestion pipeline

### 4.1 Phase 1 — Fetch

Each source implements:

```python
class Source(Protocol):
    name: str
    def fetch(self, since: datetime) -> Iterator[RawDocument]: ...
```

`RawDocument = {source, source_url, title, published_at, raw_payload, fetched_at}`.
Sources do not load text content — that belongs in Extract.

Sources in MVP:

| Source | Library | Access pattern |
|---|---|---|
| `CssfRssSource` | feedparser | Poll `/en/feed/publications?content_keyword={k}` for each configured keyword |
| `EurLexAdoptedSource` | SPARQLWrapper | SPARQL on `publications.europa.eu/webapi/rdf/sparql`, filtered by CELEX prefixes for in-force acts of interest |
| `EurLexProposalSource` | SPARQLWrapper | Same endpoint, filtered on proposals (CELEX `5*`) referencing the tracked directives/regulations |
| `LegiluxSparqlSource` | SPARQLWrapper | SPARQL on `data.legilux.public.lu/sparql` covering Mémorial A entries with financial-sector keywords |
| `LegiluxParliamentarySource` | SPARQLWrapper | SPARQL on Legilux parliamentary dossiers for LU draft bills |
| `EsmaRssSource` | feedparser | `esma.europa.eu/rss.xml` |
| `EbaRssSource` | feedparser | `eba.europa.eu/news-press/news/rss.xml` |
| `EcFismaRssSource` | feedparser | `ec.europa.eu/newsroom/fisma/feed?item_type_id={id}` for `{911, 913, 916}` and configured topic IDs |
| `CssfConsultationSource` | feedparser | CSSF main feed with title heuristic (`consultation`, `feedback`) |

Adding a new source means writing one module and registering it in `regwatch.pipeline.fetch.REGISTRY`.

### 4.2 Phase 2 — Extract

Takes `RawDocument`, returns `ExtractedDocument` with `html_text`, `pdf_path`, `pdf_extracted_text`, `pdf_is_protected`.

- HTML via `httpx` + `trafilatura` (boilerplate removal).
- PDFs downloaded and archived at `data/pdfs/{yyyy}/{mm}/{sha256[:8]}-{slug}.pdf`.
- PDF text via `pdfplumber` first, `pypdf` as fallback.
- If both extractors fail because the PDF is password-protected or encrypted with no extractable text, set `pdf_is_protected=True` and leave `pdf_extracted_text` empty. The UI surfaces these in **Settings → Manual PDF uploads** and lets the user upload an unprotected copy, which then flows through Extract again with `pdf_manual_upload=True`.

### 4.3 Phase 3 — Match

Produces `(regulation_id, method, confidence, snippet)` tuples for each extracted document.

1. **Rule matcher.** Iterates every `regulation_alias`, applies regex against title + full text. Also extracts CELEX IDs (`3\d{4}[A-Z]\d{4}`) and ELI URIs, matches those against the dedicated columns. Hits are `confidence=1.0`, `method=REGEX_ALIAS` / `CELEX_ID` / `ELI_URI`.
2. **Ollama reference extractor.** The extract text is sent to Ollama with a narrow prompt asking only for structured references (`[{ref: "CSSF 18/698", context: "..."}]`). Extracted references are re-matched against the rule aliases. `method=OLLAMA_REFERENCE`. This handles the common CSSF pattern of amendments citing the amended circular.
3. **Ollama semantic fallback.** Only runs if steps 1 and 2 produce nothing. Does a RAG query against the existing indexed regulation chunks, takes the top 5 candidates, and asks Ollama to pick the best match or return NONE. `method=OLLAMA_SEMANTIC`, `confidence` from model confidence heuristic.

### 4.4 Lifecycle classification

Runs inside Match. A new document is assigned one of:
`CONSULTATION`, `PROPOSAL`, `DRAFT_BILL`, `ADOPTED_NOT_IN_FORCE`, `IN_FORCE`, `AMENDED`, `REPEALED`.

Rules applied in order:

1. CELEX prefix rules (`5*PC` / `5*PP` → `PROPOSAL`; `3*` + application date in future → `ADOPTED_NOT_IN_FORCE`; `3*` + application date in past → `IN_FORCE`).
2. Legilux URI rules (`.../projet-de-loi/...` → `DRAFT_BILL`).
3. Title heuristics (`consultation paper`, `discussion paper`, `feedback on` → `CONSULTATION`).
4. Ollama single-letter classification prompt as a backup.

When a new document's lifecycle classification is `PROPOSAL` / `DRAFT_BILL` and it references an existing regulation, a `regulation_lifecycle_link` row is created automatically so the UI can show the relationship on both sides.

### 4.5 Severity and ICT flag

`is_ict` is keyword-heuristic: DORA, ICT, cyber, outsourcing, operational resilience, TLPT, third-party provider. Severity is bumped to `CRITICAL` when the event is an RTS/ITS or when an in-force regulation is amended. Both fields are recomputable from `raw_payload`.

### 4.6 Phase 4 — Persist

All writes happen in a single SQLite transaction per document:

1. Insert `update_event` (idempotent via `content_hash` — duplicate hashes are skipped).
2. Insert `update_event_regulation_link` rows for every match.
3. If the document belongs to an existing regulation and its `content_hash` differs from the latest `document_version`, insert a new version row and compute `change_summary` via `difflib.unified_diff` against the previous extracted text.
4. Chunk the text (RecursiveCharacterTextSplitter, 500 tokens, 50 overlap), embed each chunk via Ollama, insert into `document_chunk`, `document_chunk_vec`, `document_chunk_fts`.
5. For *newly discovered* regulations (a CELEX/ELI match that doesn't resolve to an existing row), insert a `regulation` row with `source_of_truth='DISCOVERED'` and `lifecycle_stage` set by the classifier. The user sees it in **Catalog → Discovered** and can promote or reject it.

### 4.7 Phase 5 — Notify

Writes nothing new — the inbox is a view over `update_event WHERE review_status='NEW'`, ordered by `severity DESC, published_at DESC`. The sidebar badge is derived from `COUNT(*)` on that view.

### 4.8 Pipeline run accounting

Every execution writes a `pipeline_run` row at start (`RUNNING`) and updates it at end (`COMPLETED` / `FAILED`). On startup, any lingering `RUNNING` row is marked `ABORTED`. This gives the Settings page a reliable run log and prevents stale lock state after crashes.

## 5. RAG / Q&A layer

### 5.1 Indexing

Runs as the last step of Persist. Chunks are created with `langchain-text-splitters`, embedded via Ollama `nomic-embed-text` (768-dim). The vector, sparse (FTS5), and text tables are joined by `chunk_id`.

### 5.2 Retrieval

Hybrid search per query:

1. **Dense** — embed the query, run `SELECT ... FROM document_chunk_vec WHERE embedding MATCH ? AND k = 20`.
2. **Sparse** — FTS5 BM25 over `document_chunk_fts`, top 20.
3. **Reciprocal Rank Fusion** — merge the two top-20 lists to top 10 (`score = Σ 1 / (60 + rank)`).
4. **Optional re-ranker** — Ollama chat call with a short re-ranking prompt, disabled by default.

Pre-filters on `authorization_types`, `is_ict`, `lifecycle_stage`, `regulation_id` are applied as WHERE clauses before the vector search.

### 5.3 Answering

- System prompt instructs the model to answer only from the provided context, cite sources by (regulation reference, version date, chunk id), and decline to answer if context is insufficient.
- Streaming responses via Server-Sent Events to the HTMX chat view.
- Each assistant message stores its `retrieved_chunk_ids` in `chat_message.retrieved_chunk_ids`, so the user can click through to the source chunk highlighted in the original document.
- Hard guard: if retrieval returns zero chunks above a minimum score, the backend responds directly with "I could not find relevant information in the indexed regulations" and never calls the LLM.

## 6. UI

### 6.1 Navigation (Layout A — sidebar)

```
RegWatch
├── 📊 Dashboard                 — KPIs, deadline timeline, recent activity
├── 📬 Inbox              [ n ]  — new updates triage
│     ├── All · Critical · Material · Archived
├── 📋 Catalog                   — the regulation list
│     ├── All · AIFM · Chapter 15 ManCo · Shared · Discovered
├── ⚡ ICT / DORA
│     ├── All · ICT Risk Mgmt · Incident Reporting · Resilience Testing
│     ├── Third-Party Risk · Info Sharing
├── 📝 Drafts & Upcoming  [ n ]  — CONSULTATION / PROPOSAL / DRAFT_BILL / ADOPTED
├── ⏰ Deadlines                 — sorted by transposition_deadline / application_date
├── 💬 Q&A Chat                  — RAG interface, sessions + chat
└── ⚙  Settings
      ├── Sources & schedules · Ollama models · Seed catalog
      ├── Manual PDF uploads · Pipeline runs (log)
```

### 6.2 Key views

**Dashboard** — four KPI tiles (Catalog in force, Inbox new, Drafts upcoming, ICT/DORA tracked), an "Upcoming deadlines" widget listing items due in the next 24 months sorted by urgency, and a "Recent activity" feed of the latest pipeline results.

**Inbox** — list of new `update_event` rows with severity colour-coding, badges for source and ICT, and triage actions (`Mark seen`, `Assess`, `Archive`). Clicking an event opens the detail panel with linked regulations, match method, and a "Compare to previous version" button that opens the diff.

**Catalog / Regulation detail** — breadcrumbs, a header with lifecycle badge and applicability tags, a **diff view** (unified_diff coloured as added/removed), a list of linked `update_event`s, and a **timeline widget** showing the regulation's lifecycle (publication → amendments → expected changes) with filled dots for past events and hollow dots for upcoming ones.

**Drafts & Upcoming** — a filtered catalog view restricted to `lifecycle_stage IN (CONSULTATION, PROPOSAL, DRAFT_BILL, ADOPTED_NOT_IN_FORCE)` with a countdown column for the nearest deadline per item.

**Deadlines** — dedicated sorted list pulling from `transposition_deadline` and `application_date` across all regulations. Colour bands for 0–30 days (red), 30–180 days (amber), 180–730 days (blue), > 2 years (grey).

**Q&A Chat** — sessions list on the left, current chat on the right. Active filters (authorisation, lifecycle, ICT) are shown below the title and affect retrieval. Responses stream in via SSE, with citations rendered as clickable chips that jump to the source chunk in the version detail view.

**Settings** — read-only view of `config.yaml`, per-job "Run now" buttons, Ollama connectivity badge, manual PDF upload form for flagged protected documents, and a tail view of the `pipeline_run` log.

### 6.3 Frontend approach

- Jinja2 templates rendered server-side.
- HTMX for interactivity (inbox triage, filter changes, chat streaming, pagination).
- Tailwind CSS via CDN — no build step.
- Alpine.js loaded only on pages that need small amounts of client state (modals, tab strips).

No Node build chain, no bundler, no React/Vue. The entire frontend lives inside the FastAPI process.

## 7. Scheduler and configuration

### 7.1 APScheduler jobs

| Job | Interval | Sources |
|---|---|---|
| `run_pipeline_cssf` | 6 h | `cssf_rss`, `cssf_consultation` |
| `run_pipeline_eu` | 6 h | `eur_lex_adopted`, `eur_lex_proposal` |
| `run_pipeline_lu` | 12 h | `legilux_sparql`, `legilux_parliamentary` |
| `run_pipeline_esma_eba_fisma` | 6 h | `esma_rss`, `eba_rss`, `ec_fisma_rss` |
| `recompute_deadlines` | daily 06:00 | no fetch — refreshes the Dashboard deadlines view from SQLite |
| `health_check_ollama` | 30 min | no fetch — pings `localhost:11434/api/tags` and updates the UI badge status |

Every source registered in `regwatch.pipeline.fetch.REGISTRY` must be assigned to a job. A startup check fails loudly if an enabled source has no job mapping.

All intervals live in `config.yaml`. The Settings → Sources & schedules page shows the current interval, the last run, its status, and a manual "Run now" button. Jobs are idempotent via content hash, so "Run now" never creates duplicates.

### 7.2 `config.yaml`

Single YAML file at the project root. Committed templated version: `config.example.yaml`. Real config is git-ignored. Fields cover entity, sources (enable flags, intervals, keywords, CELEX prefixes, topic IDs), Ollama (URL, models, prompt version), RAG (chunk sizes, retrieval parameters, rerank flag), paths, and UI (language, timezone).

## 8. Project structure

```
RegulatoryWatcher/
├── pyproject.toml
├── README.md
├── config.yaml                  # gitignored
├── config.example.yaml          # committed
├── data/                        # gitignored
│   ├── app.db
│   ├── pdfs/
│   └── uploads/
├── seeds/
│   └── regulations_seed.yaml    # curated catalog from research
├── alembic/
│   └── versions/
├── regwatch/
│   ├── main.py                  # FastAPI app + lifespan
│   ├── config.py                # pydantic-settings, loads config.yaml
│   ├── db/
│   │   ├── engine.py            # engine + sqlite-vec loader + FTS5
│   │   ├── models.py            # SQLAlchemy models
│   │   └── seed.py              # initial seed loader
│   ├── domain/
│   │   └── types.py             # RawDocument, ExtractedDocument, MatchedDocument
│   ├── pipeline/
│   │   ├── runner.py
│   │   ├── fetch/
│   │   │   ├── base.py
│   │   │   ├── cssf_rss.py
│   │   │   ├── eur_lex_adopted.py
│   │   │   ├── eur_lex_proposal.py
│   │   │   ├── legilux_sparql.py
│   │   │   ├── legilux_parliamentary.py
│   │   │   ├── esma_rss.py
│   │   │   ├── eba_rss.py
│   │   │   ├── ec_fisma_rss.py
│   │   │   └── cssf_consultation.py
│   │   ├── extract/
│   │   │   ├── html.py
│   │   │   └── pdf.py
│   │   ├── match/
│   │   │   ├── rules.py
│   │   │   ├── lifecycle.py
│   │   │   └── ollama_refs.py
│   │   ├── persist.py
│   │   └── notify.py
│   ├── rag/
│   │   ├── chunker.py
│   │   ├── embeddings.py
│   │   ├── retrieval.py
│   │   └── answer.py
│   ├── scheduler/
│   │   └── jobs.py
│   ├── services/
│   │   ├── regulations.py
│   │   ├── updates.py
│   │   ├── inbox.py
│   │   ├── chat.py
│   │   └── deadlines.py
│   ├── web/
│   │   ├── routes/
│   │   ├── templates/
│   │   └── static/
│   └── cli.py
└── tests/
    ├── unit/
    ├── integration/
    ├── fixtures/
    └── conftest.py
```

### CLI commands

```
regwatch init-db                   Create schema, load sqlite-vec and FTS5
regwatch seed                      Load seeds/regulations_seed.yaml
regwatch run-pipeline               Run all sources once
regwatch run-pipeline --source X    Run a single source
regwatch reindex                    Rebuild embeddings for all versions
regwatch chat "..."                Run a one-shot RAG query from the terminal
regwatch dump-pipeline-runs        Tail the last 20 pipeline runs
```

## 9. Testing strategy

### 9.1 Unit tests

- `test_rules_matcher.py` — regex aliases against curated title/snippet variants (`"Circular CSSF 18-698"`, `"CSSF-RS 18/698"`, `"circular 18/698 of 23 August 2018"` all resolve to the same `regulation_id`).
- `test_lifecycle_classifier.py` — CELEX prefix, Legilux URI, and title heuristic tables.
- `test_chunker.py` — chunk sizes, overlaps, edge cases (very short and very long texts).
- `test_pdf_protection_detection.py` — flags a protected PDF, passes an unprotected one through.
- `test_diff_generator.py` — checks `unified_diff` output against expected hunks.

### 9.2 Integration tests

- `test_pipeline_end_to_end.py` — fake sources + fake Ollama. Asserts the exact set of DB rows written, including on a second run with modified content (diff and new version).
- `test_persist_idempotency.py` — same raw document twice, asserts one `update_event`.
- `test_retrieval_hybrid.py` — sqlite-vec + FTS5 against a small in-memory corpus, asserts both dense and sparse hits appear in the RRF result and pre-filters work.
- `test_catalog_service.py` — service-layer queries against a seeded test DB.

### 9.3 Contract tests

Recorded fixtures of real RSS / SPARQL responses in `tests/fixtures/`. No live HTTP in CI. Live tests are marked `@pytest.mark.live` and excluded from default runs; they exist only for manual smoke checks after library upgrades.

### 9.4 Prompt snapshot tests

Every Ollama prompt is a versioned template. Unit tests assert the rendered string against a stored snapshot so unintended wording drift is caught.

### 9.5 Test infrastructure

- pytest + pytest-asyncio + pytest-httpx.
- Fresh SQLite per test in `tmp_path` with `sqlite-vec` and FTS5 loaded.
- `fake_ollama` fixture mocks `POST /api/chat` and `POST /api/embeddings`. Embeddings are deterministic via a hash-based function, producing stable vectors without requiring an Ollama process.
- `seed_db` fixture loads a 10-regulation subset of the production seed for speed.

### 9.6 Explicitly out of scope for MVP

- LLM behaviour tests against a real Ollama model (non-deterministic and flaky).
- Playwright end-to-end UI tests (HTMX endpoints are simple enough that FastAPI TestClient fragment assertions suffice).
- Performance benchmarks (pipeline is hourly scale, DB stays under ~100 MB, RAG queries are sub-second locally).

## 10. Risks and open points

- **Legilux parliamentary dossiers SPARQL shape.** The parliamentary dataset is less well documented than Mémorial A. The spec assumes a query can be constructed; if not, the MVP can fall back to scraping the dossier listing page, tracked as an open decision for the implementation plan.
- **CSSF consultation feed.** There is no dedicated keyword feed for consultations. The MVP uses the main feed with title heuristics; this may miss items and will need review after the first weeks of operation.
- **Password-protected CSSF PDFs.** The frequency of protected PDFs is unknown. If it turns out to be common, the "manual upload" fallback becomes critical UX and may need a more prominent place than the Settings page.
- **Ollama model sizing.** `llama3.1:8b` is the default but assumes the user's machine can run it comfortably. If the user is on CPU-only and finds it too slow, a smaller model (`qwen2.5:3b`) is a one-line config change.
- **Seed catalog completeness.** The initial seed is hand-curated from the research. It will not be exhaustive on day one; the discovered-regulations flow is the safety net and should be reviewed in the first weeks.

## 11. Decisions index

| # | Decision | Choice |
|---|---|---|
| 1 | Scope | One legal entity (Union Investment Luxembourg S.A.) with two CSSF authorisations |
| 2 | Users | Single user, local, no authentication |
| 3 | UI | Local browser web app, Layout A (sidebar navigation) |
| 4 | Stack | Python + FastAPI + SQLite + sqlite-vec + Jinja2 + HTMX + Tailwind |
| 5 | Catalog | Curated seed + automatic expansion via discovery flow |
| 6 | Scheduler | In-process APScheduler |
| 7 | Versioning | Full history with stored diffs and version timeline |
| 8 | Sources MVP | CSSF RSS, EUR-Lex SPARQL (adopted + proposals), Legilux SPARQL (Mémorial A + parliamentary), ESMA RSS, EBA RSS, EC-FISMA RSS, CSSF consultation heuristic |
| 9 | Matching | Rule-based (regex / CELEX / ELI) + Ollama reference extraction + Ollama semantic fallback |
| 10 | Content depth | Metadata + HTML text + extracted PDF text + vector index + manual upload fallback for protected PDFs |
| 11 | Notifications | In-app inbox only |
| 12 | UI language | English |
| 13 | ICT flag | Boolean `is_ict` + dedicated sidebar tab + optional `dora_pillar` sub-category |
| 14 | Draft tracking | Explicit `lifecycle_stage` enum, `regulation_lifecycle_link` for proposal/transposition relationships, dedicated "Drafts & Upcoming" and "Deadlines" views |
| 15 | Pipeline shape | Five-phase pipeline with source plugins and source-agnostic downstream phases |
| 16 | Vector store | sqlite-vec (same `app.db` as metadata, single-file backup) |
| 17 | LLM | Local Ollama (`llama3.1:8b` chat, `nomic-embed-text` embeddings) |
| 18 | Config | Single `config.yaml`, read-only in UI |
