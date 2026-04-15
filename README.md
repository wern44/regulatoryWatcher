# Regulatory Watcher

Local single-user tool that monitors CSSF, EU, and Luxembourg regulatory sources for Union Investment Luxembourg S.A. (LEI `529900FSORICM1ERBP05`).

It ingests updates from CSSF, EUR-Lex, Legilux, ESMA, EBA, and EC-FISMA, matches them against a curated catalog of in-force regulations, detects changes (with full version history and unified diffs), flags ICT/DORA items, and surfaces drafts and upcoming deadlines. A local RAG layer (Ollama + sqlite-vec + FTS5) lets you ask grounded, cited questions about the indexed regulations.

See `docs/superpowers/specs/2026-04-08-regulatory-watcher-design.md` for the full design and `docs/superpowers/plans/2026-04-08-regulatory-watcher.md` for the implementation plan.

## Requirements

- Python 3.11+
- A running Ollama instance (default `http://localhost:11434`) with `llama3.1:8b` and `nomic-embed-text` pulled

## Setup

    python -m venv .venv
    . .venv/Scripts/activate     # Windows
    # source .venv/bin/activate  # Linux / macOS
    pip install -e .[dev]
    cp config.example.yaml config.yaml
    # Edit config.yaml to point at your data directory and Ollama instance.

## CLI usage

Initialise the database and load the curated regulation catalog:

    regwatch init-db
    regwatch seed

Run one pass of the pipeline across all enabled sources (or a single source):

    regwatch run-pipeline
    regwatch run-pipeline --source cssf_rss

Re-chunk and re-embed every current document version (use after changing the embedding model or chunk size):

    regwatch reindex

Ask a grounded question against the indexed regulations:

    regwatch chat "What does Article 24 of DORA require?"

Inspect recent pipeline runs:

    regwatch dump-pipeline-runs --tail 20

## Web UI

    uvicorn regwatch.main:app --reload

Then open http://localhost:8000. The sidebar exposes Dashboard, Inbox, Catalog, ICT / DORA, Drafts, Deadlines, Q&A Chat, and Settings views.

## Testing

    pytest
    pytest tests/unit            # unit tests only
    pytest tests/integration     # integration tests only

Integration tests use a fresh SQLite file per test and mock only Ollama and outbound HTTP.
# regulatoryWatcher
