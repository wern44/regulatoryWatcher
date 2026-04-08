# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Local single-user Python tool that monitors CSSF, EU, and Luxembourg regulatory sources for Union Investment Luxembourg S.A. (LEI `529900FSORICM1ERBP05`). It ingests updates, matches them against a curated catalog, tracks version history, flags ICT/DORA items, and exposes a FastAPI + Jinja/HTMX web UI plus a Typer CLI. A RAG layer (Ollama + sqlite-vec + FTS5) answers grounded questions about the indexed regulations.

Authoritative design and plan live in `docs/superpowers/specs/2026-04-08-regulatory-watcher-design.md` and `docs/superpowers/plans/2026-04-08-regulatory-watcher.md` — re-read them when making architectural decisions.

## Common commands

All commands assume the venv at `.venv` is active (`. .venv/Scripts/activate` on Windows).

    pytest                                            # full suite (~130 tests, ~6s)
    pytest tests/unit                                 # unit tests only
    pytest tests/integration                          # integration tests only
    pytest tests/unit/test_rules_matcher.py -v        # single file
    pytest tests/unit/test_rules_matcher.py::test_matches_celex_id  # single test
    pytest -m live                                    # live network tests (excluded by default)
    ruff check regwatch                               # lint
    ruff check regwatch --fix                         # lint + autofix
    mypy regwatch                                     # strict type check
    regwatch init-db && regwatch seed                 # first-time DB setup
    regwatch run-pipeline [--source NAME]             # one synchronous pipeline pass
    regwatch chat "..."                               # one-shot RAG Q&A
    regwatch dump-pipeline-runs --tail 20             # inspect recent runs
    uvicorn regwatch.main:app --reload                # run the web UI on :8000

## Architecture — the things you cannot derive from any single file

### Five-phase pipeline, source-agnostic downstream

The ingest path is a five-phase pipeline — **Fetch → Extract → Match → Persist → Notify** — wired together in `regwatch/pipeline/pipeline_factory.py::build_runner`. Source plugins in `regwatch/pipeline/fetch/` are the ONLY phase that knows about a specific feed/endpoint. Everything downstream is source-agnostic and operates on the `RawDocument → ExtractedDocument → MatchedDocument` dataclasses in `regwatch/domain/types.py`. When adding a new source, implement the `Source` protocol in `regwatch/pipeline/fetch/base.py` and decorate with `@register_source` — it will appear in `REGISTRY` by its `name` class attribute.

`regwatch/pipeline/sources.py` (`import_all_sources`, `build_enabled_sources`) is the single place that knows how to instantiate each registered source from `AppConfig`. Both the CLI `run-pipeline` command and the web UI "Run pipeline now" button go through it. Adding a new source with non-default constructor args means extending `instantiate_source` here.

### Matcher fallback chain

`CombinedMatcher` (`regwatch/pipeline/match/combined.py`) runs rule-based matching first (`rules.py` — regex aliases, CELEX IDs, ELI URIs from the `regulation_alias` / `regulation.celex_id` / `regulation.eli_uri` columns), and only calls Ollama (`ollama_refs.py`) when rules find nothing. On any `httpx.HTTPError` / `OllamaError` it latches Ollama off for the rest of that matcher's lifetime — **do not re-raise Ollama errors from match paths**, doing so will turn a missing model into a per-document traceback storm.

### Persistence is idempotent by content hash

`regwatch/pipeline/persist.py` hashes `(pdf_extracted_text or html_text)` and skips the insert entirely if an `update_event` with that hash already exists. New `document_version` rows are only created when the hash differs from the current version for that regulation; `change_summary` is a unified diff (`regwatch/pipeline/diff.py`) against the previous version's text. This means safe re-runs of `run-pipeline` are a core invariant — preserve it.

### Service layer vs. ORM boundary

`regwatch/services/` owns all use-case functions consumed by the web routes and the CLI. Services return **plain dataclasses (DTOs), not ORM rows**, so the web layer never has to worry about session lifecycle. The re-export `regwatch/services/chat.py → regwatch.rag.chat_service.ChatService` is deliberate so the web can import everything from `regwatch.services.*`. When adding a new service, follow the existing pattern: accept a `Session` in the constructor, return `@dataclass` DTOs from methods.

### SQLite configuration is load-bearing

`regwatch/db/engine.py` does three non-default things that matter:

1. **`poolclass=NullPool`** — every `Session` gets a fresh DBAPI connection. SQLAlchemy's default `SingletonThreadPool` reuses one connection per thread, which in a long-running uvicorn worker leaks transaction state across requests and defeats PRAGMA changes made after the process started. Do not change this without understanding the implications.
2. **`sqlite_vec.load(dbapi_conn)`** in the `connect` event — required for the vector table to work. Every new connection loads the extension.
3. **`PRAGMA busy_timeout=10000`** — without this, concurrent writers (CLI + uvicorn, or two uvicorns) fail instantly with `database is locked`.

Virtual tables (`document_chunk_vec` for sqlite-vec, `document_chunk_fts` for FTS5) are created by `regwatch/db/virtual_tables.py::create_virtual_tables`, which also installs FTS5 sync triggers. They are created separately from `Base.metadata.create_all` and must be re-created when the embedding dimension changes.

### RAG retrieval: pool + hydrate, not filter in SQL

`regwatch/rag/retrieval.py::HybridRetriever` deliberately runs dense (sqlite-vec MATCH) and sparse (FTS5 MATCH) searches **without** WHERE-filter binds, fuses with reciprocal rank fusion, and then applies filters (is_ict, lifecycle, authorization) in Python during hydration. This is intentional: sqlite-vec's `MATCH` has strict parameter-binding rules that don't combine cleanly with SQLAlchemy expanding bindparams. Keep filtering client-side unless you have a concrete reason.

FTS5 queries are sanitized via `_sanitize_fts_query` which strips punctuation and wraps bare terms into an OR-joined quoted expression — natural-language questions (e.g. "What is DORA?") contain `?` which FTS5 interprets as a special character.

### App state and the session factory

`regwatch/main.py::create_app` builds the engine, runs `Base.metadata.create_all` + `create_virtual_tables`, and stashes `config`, `session_factory`, and `ollama_client` on `app.state`. All web routes get their DB session via `request.app.state.session_factory()` and their Ollama via `request.app.state.ollama_client`. For tests, override `client.app.state.ollama_client = MagicMock()` after creating the TestClient. Never import the session factory directly.

## Testing conventions

- **Integration tests hit a fresh SQLite file in `tmp_path`** — never mock the database. See `tests/integration/test_app_smoke.py::_client` for the standard app-under-test helper; it rewrites `config.example.yaml` into `tmp_path` and reloads `regwatch.main` to force `create_app()` to re-read `REGWATCH_CONFIG`. Because the default config enables every fetch source and those sources hit the network in `fetch()`, tests that invoke the real pipeline (e.g. `test_run_pipeline_action.py`) must disable all sources except the one they're exercising — see `_cssf_only_client` for the pattern.
- **Mock only Ollama and outbound HTTP.** Use `pytest-httpx` for HTTP (`httpx_mock` fixture) and `MagicMock` for `OllamaClient`. When mocking `OllamaClient.embed`, return a vector with the config's `embedding_dim` (768 for the example config) or sqlite-vec will reject it.
- **The `live` marker** (`pytest -m live`) is reserved for tests that hit real external services — these are excluded from default `pytest` runs via `addopts = "-m 'not live'"` in `pyproject.toml`.
- **Unified-diff assertions** use `-oldline` / `+newline` (no space after the sign) — that's what Python's `difflib.unified_diff` actually emits.

## Conventions to preserve

- **No backward-compat shims.** When changing a function signature used only internally, change every caller — don't keep the old signature as a wrapper. Feature flags and `_deprecated_` renames are not used in this codebase.
- **Commits are small and per-task.** The plan in `docs/superpowers/plans/` is structured as Task N → write failing test → implement → run tests → commit. Preserve that cadence for new work.
- **Line-ending warnings** from git (`LF will be replaced by CRLF`) are expected on Windows and can be ignored.
- **`config.yaml` is gitignored**; `config.example.yaml` is the committed template. When changing config schema, update both `config.example.yaml` and `regwatch/config.py`.
