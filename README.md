# Regulatory Watcher

Local single-user tool that monitors CSSF, EU, and Luxembourg regulatory sources for Union Investment Luxembourg S.A.

See `docs/superpowers/specs/2026-04-08-regulatory-watcher-design.md` for the full design.

## Quick start

    python -m venv .venv
    . .venv/Scripts/activate   # Windows
    pip install -e .[dev]
    cp config.example.yaml config.yaml
    regwatch init-db
    regwatch seed
    uvicorn regwatch.main:app --reload

Open http://localhost:8000
