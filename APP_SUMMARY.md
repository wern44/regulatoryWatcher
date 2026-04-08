# Regulatory Watcher — App Summary

A friendly overview for new users: what the app does, where its data comes from, how to set it up on a Debian server, and what each pipeline operation is for.

---

## 1. What the app is

**Regulatory Watcher** is a local, single-user Python tool that monitors regulatory publications from CSSF, EU, and Luxembourg authorities and turns them into an actionable inbox, a version-tracked catalog, a deadline list, and a chat interface you can ask questions against.

It is built as a FastAPI + HTMX web UI with a Typer CLI. All data lives in **one SQLite file** (`data/app.db`), including the vector index for question answering. There is no external database, no message broker, and no authentication system — it's designed to run quietly on a single machine for a single person.

> **Important:** There is **no built-in authentication**. If you deploy it on a server, either bind it to `127.0.0.1` and access it via SSH tunnel, restrict it to a private network, or put it behind a reverse proxy that enforces HTTP basic auth. Never expose it directly to the public internet.

---

## 2. How the app works (end-to-end)

### The five-phase pipeline

Every update the tool discovers goes through the same five phases:

| Phase | What happens | Where it lives |
|---|---|---|
| **1. Fetch** | A source plugin hits a feed or SPARQL endpoint and yields `RawDocument` items (title, URL, publication date, raw payload). Items older than the "since" cutoff are dropped. | `regwatch/pipeline/fetch/` |
| **2. Extract** | For HTML pages, `trafilatura` strips boilerplate and pulls the article body. For PDFs, the file is downloaded, archived under `data/pdfs/YYYY/MM/`, and text is extracted with `pdfplumber` (falling back to `pypdf`). Password-protected PDFs are flagged so you can upload an unlocked copy manually via the Settings page. | `regwatch/pipeline/extract/` |
| **3. Match** | Rule-based matching runs first: regex aliases, CELEX IDs, ELI URIs are looked up against the curated `regulation` catalog. If nothing matches, Ollama (`llama3.1:latest`) is asked to extract structured references from the text, which are then re-resolved through the same rules. The matcher also classifies the lifecycle stage (IN_FORCE, PROPOSAL, DRAFT_BILL, CONSULTATION, ADOPTED_NOT_IN_FORCE), flags ICT/DORA items, and assigns a severity (INFORMATIONAL / MATERIAL / CRITICAL). | `regwatch/pipeline/match/` |
| **4. Persist** | Idempotent by content hash: if the same text has already been seen, the pipeline skips it. Otherwise a new `update_event` row is created, linked to matched regulations, and a new `document_version` row is inserted with a unified-diff `change_summary` against the previous version. | `regwatch/pipeline/persist.py` |
| **5. Notify** | Notifications are in-app only. New events appear in the **Inbox** view with severity colour coding; you triage them with Mark-seen / Archive buttons. | Inbox view (`regwatch/web/routes/inbox.py`) |

### The RAG layer

In parallel, each new document version can be **chunked** (≈500 tokens, 50 token overlap), **embedded** via Ollama's `nomic-embed-text`, and indexed into two SQLite virtual tables:

- `document_chunk_vec` — dense semantic index (sqlite-vec extension)
- `document_chunk_fts` — sparse keyword index (SQLite FTS5)

When you ask a question in the **Q&A Chat** page (or via `regwatch chat "..."`), both indexes are queried, results are merged by reciprocal rank fusion, and the top chunks are sent to `llama3.1` as context. The answer comes back with clickable citations that jump to the source chunk.

---

## 3. Where updates come from

Nine source plugins are included. Each one is independent: one failing source does not block the others.

| Source name | What it watches | Default interval |
|---|---|---|
| `cssf_rss` | CSSF Publications RSS feed, filtered by keywords (`aif`, `ucits`, `aml-cft`, `sustainable-finance`, `emir`, `mifid`, `investment-fund`, `crypto-assets`) | 6 h |
| `cssf_consultation` | Same CSSF feed, but filtered client-side for titles / descriptions containing `consultation`, `discussion paper`, or `feedback` | 6 h |
| `eur_lex_adopted` | EUR-Lex CELLAR SPARQL endpoint, restricted to a configurable list of CELEX prefixes (AIFMD, UCITS, DORA, SFDR, Taxonomy, AIFMD II by default) | 6 h |
| `eur_lex_proposal` | EUR-Lex CELLAR SPARQL, proposals only (CELEX prefix `5…`) | 6 h |
| `legilux_sparql` | Legilux (Luxembourg official gazette) SPARQL endpoint, filtered to financial-sector laws | 12 h |
| `legilux_parliamentary` | Legilux parliamentary dossiers (`projet-de-loi`) — draft bills in the Chamber of Deputies | 12 h |
| `esma_rss` | ESMA news RSS feed | 6 h |
| `eba_rss` | EBA news RSS feed | 6 h |
| `ec_fisma_rss` | European Commission FISMA newsroom, one feed per configured `item_type_id` and `topic_id`, deduplicated by link | 6 h |

Each source's interval, enablement, and tuning parameters live under `sources:` in `config.yaml`. See `config.example.yaml` for the full default set.

### When do updates actually get pulled?

**This is the most important thing to understand about the current version of the tool**:

- **The APScheduler jobs are built on startup but not started.** The in-process scheduler is a placeholder for future work — clicking through the UI will not trigger periodic fetches by itself.
- **In practice today, updates are pulled in one of two ways**:
  1. **Manually**, by clicking the **Run pipeline now** button on the Dashboard (or by running `regwatch run-pipeline` from the CLI).
  2. **On a schedule**, by pointing an external scheduler (cron or a systemd timer) at `regwatch run-pipeline`. This is the recommended approach on a server — see the Debian install section below.

The per-source `interval_hours` values in the config are currently only used as hints; they do not yet wire into the live scheduler.

---

## 4. Pipeline operations — what each command does and when to use it

All pipeline operations run through the `regwatch` CLI (installed as a console script when you `pip install -e .`). Every command accepts `--config PATH` to point at a specific config file.

### `regwatch init-db`
Creates the SQLite database, all tables, the sqlite-vec virtual table, and the FTS5 virtual table + sync triggers. Safe to re-run: it only creates what's missing.

**Use it when:** first-time setup, or after deleting `data/app.db`.

### `regwatch seed [--file seeds/regulations_seed.yaml]`
Loads the curated regulation catalog from a YAML file — the ~10 core regulations the tool knows about (AIFMD, UCITS, DORA, SFDR, CSSF 18/698, etc.), along with their aliases (regex patterns), CELEX IDs, ELI URIs, and applicability (AIFM / Chapter 15 ManCo / BOTH). The loader is idempotent: you can re-run it to pick up catalog edits without duplicating rows.

**Use it when:** first-time setup, or after editing `seeds/regulations_seed.yaml` to add/update regulations.

### `regwatch run-pipeline [--source NAME]`
Runs one full pass of the five-phase pipeline across all enabled sources. Each source is isolated: if `esma_rss` times out, the run still completes with the events `cssf_rss` fetched. Writes a new `pipeline_run` row recording which sources were attempted, which failed, and how many events / document versions were created.

If you pass `--source cssf_rss`, only that source is activated for this run.

**Use it when:**
- **Manually,** to check for updates right now.
- **On a schedule** (cron / systemd timer) to pull updates periodically.
- After adding or editing a source, to verify the change end-to-end.
- **Performance note:** a full pass can take several minutes because every document with no rule match goes through Ollama for reference extraction (~10–30 s per document on CPU).

### `regwatch reindex`
Drops every `document_chunk`, `document_chunk_vec`, and `document_chunk_fts` row, then re-chunks and re-embeds every current document version. This rebuilds the RAG layer from scratch.

**Use it when:**
- You change `ollama.embedding_model` or `ollama.embedding_dim` in the config.
- You change `rag.chunk_size_tokens` or `rag.chunk_overlap_tokens`.
- The RAG answers look stale or broken and you want a clean rebuild.
- **Not for fetching new content** — reindex does not touch the pipeline.

### `regwatch chat "your question"`
One-shot retrieval-augmented Q&A. Embeds your question, retrieves the top chunks by hybrid search (dense + sparse + RRF), sends them to `llama3.1` as context, and prints the grounded answer with the list of cited chunk IDs. Uses default filters (no ICT restriction, both authorization types, any lifecycle stage).

**Use it when:** you want to test RAG from the terminal without opening the browser, or you want to script "ask the regulations" questions.

### `regwatch dump-pipeline-runs [--tail N]`
Prints the most recent N entries from the `pipeline_run` table as a plain table: run id, status, events created, versions created, start time.

**Use it when:** you want to see whether your scheduled runs are succeeding, or you're debugging why the inbox is empty.

---

## 5. Installing on a Debian server

These instructions target **Debian 12 (Bookworm)**. They assume a fresh server, a non-root user with sudo rights, and that you will access the UI through an SSH tunnel (recommended) or a reverse proxy.

### 5.1 System packages

    sudo apt update
    sudo apt install -y \
        python3 python3-venv python3-pip \
        build-essential \
        git \
        curl \
        sqlite3 \
        ca-certificates

`build-essential` is needed because a few Python dependencies (`tiktoken`, `reportlab`) compile C extensions.

### 5.2 Create a dedicated system user

    sudo useradd --system --create-home --shell /usr/sbin/nologin regwatch
    sudo mkdir -p /opt/regwatch
    sudo chown regwatch:regwatch /opt/regwatch

### 5.3 Install Ollama

Ollama runs locally and serves the chat + embedding models on `http://127.0.0.1:11434`.

    curl -fsSL https://ollama.com/install.sh | sh

The installer registers a systemd service (`ollama.service`) that starts Ollama on boot. Verify it's up:

    systemctl status ollama
    curl -s http://127.0.0.1:11434/api/tags

Pull the two models the tool needs:

    sudo -u regwatch OLLAMA_HOST=127.0.0.1:11434 ollama pull llama3.1:latest
    sudo -u regwatch OLLAMA_HOST=127.0.0.1:11434 ollama pull nomic-embed-text

> `llama3.1:latest` is ~5 GB and `nomic-embed-text` is ~270 MB. Plan disk and RAM accordingly: Ollama needs ~8 GB RAM to run `llama3.1:8b` smoothly on CPU. If the server has less RAM, swap in a smaller model (e.g. `qwen2.5:3b`) and update `chat_model` in `config.yaml`.

### 5.4 Clone and install the app

    sudo -u regwatch -H bash
    cd /opt/regwatch
    git clone https://github.com/wern44/regulatoryWatcher.git app
    cd app
    python3 -m venv .venv
    . .venv/bin/activate
    pip install --upgrade pip
    pip install -e '.[dev]'
    exit  # drop back to your normal user

### 5.5 Configure

    sudo -u regwatch cp /opt/regwatch/app/config.example.yaml /opt/regwatch/app/config.yaml
    sudo -u regwatch nano /opt/regwatch/app/config.yaml

Minimum edits:

- `paths.db_file`, `paths.pdf_archive`, `paths.uploads_dir` — set to absolute paths under `/opt/regwatch/data/` so the data lives outside the app checkout.
- `ollama.chat_model` — confirm it matches what you pulled.
- `ui.host` — leave as `127.0.0.1` if you plan to SSH-tunnel; only change this if you're fronting the app with a reverse proxy.
- `ui.timezone` — `Europe/Luxembourg` is the default; adjust if your server is elsewhere.

Create the data directory and initialise the database:

    sudo mkdir -p /opt/regwatch/data/pdfs /opt/regwatch/data/uploads
    sudo chown -R regwatch:regwatch /opt/regwatch/data
    sudo -u regwatch -H bash -c 'cd /opt/regwatch/app && . .venv/bin/activate && regwatch init-db && regwatch seed'

### 5.6 Run the web UI as a systemd service

Create `/etc/systemd/system/regwatch-web.service`:

    [Unit]
    Description=Regulatory Watcher web UI
    After=network.target ollama.service
    Requires=ollama.service

    [Service]
    Type=simple
    User=regwatch
    Group=regwatch
    WorkingDirectory=/opt/regwatch/app
    Environment=REGWATCH_CONFIG=/opt/regwatch/app/config.yaml
    ExecStart=/opt/regwatch/app/.venv/bin/uvicorn regwatch.main:app --host 127.0.0.1 --port 8000
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

Enable and start it:

    sudo systemctl daemon-reload
    sudo systemctl enable --now regwatch-web
    sudo systemctl status regwatch-web

Verify it's serving:

    curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/
    # Expect: 200

### 5.7 Schedule the pipeline with a systemd timer

Because the in-process APScheduler isn't live-wired yet, the reliable way to get periodic fetches is a systemd timer that runs the CLI.

Create `/etc/systemd/system/regwatch-pipeline.service`:

    [Unit]
    Description=Regulatory Watcher pipeline run
    After=network.target ollama.service regwatch-web.service

    [Service]
    Type=oneshot
    User=regwatch
    Group=regwatch
    WorkingDirectory=/opt/regwatch/app
    Environment=REGWATCH_CONFIG=/opt/regwatch/app/config.yaml
    ExecStart=/opt/regwatch/app/.venv/bin/regwatch run-pipeline

Create `/etc/systemd/system/regwatch-pipeline.timer`:

    [Unit]
    Description=Run Regulatory Watcher pipeline every 6 hours

    [Timer]
    OnBootSec=10min
    OnUnitActiveSec=6h
    Persistent=true

    [Install]
    WantedBy=timers.target

Enable and start the timer:

    sudo systemctl daemon-reload
    sudo systemctl enable --now regwatch-pipeline.timer
    systemctl list-timers regwatch-pipeline.timer

The first run happens 10 minutes after boot; subsequent runs every 6 hours. `Persistent=true` means missed runs (e.g. after a reboot) are caught up.

### 5.8 Access the UI

Since `ui.host` is `127.0.0.1`, the app is not reachable from outside the server by design. Two common options:

**Option A — SSH tunnel (simplest, single user):**

From your laptop:

    ssh -N -L 8000:127.0.0.1:8000 your-user@your-server

Then open `http://localhost:8000` in your browser.

**Option B — Reverse proxy with basic auth (nginx):**

    sudo apt install -y nginx apache2-utils
    sudo htpasswd -c /etc/nginx/regwatch.htpasswd youruser
    sudo nano /etc/nginx/sites-available/regwatch

Paste:

    server {
        listen 80;
        server_name regwatch.internal.example;  # your internal hostname

        location / {
            auth_basic "Regulatory Watcher";
            auth_basic_user_file /etc/nginx/regwatch.htpasswd;
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_read_timeout 600s;  # pipeline runs can be slow
        }
    }

Enable and reload:

    sudo ln -s /etc/nginx/sites-available/regwatch /etc/nginx/sites-enabled/
    sudo nginx -t && sudo systemctl reload nginx

Add TLS with Let's Encrypt only if the hostname is actually reachable from the public internet — for an internal tool, keep it HTTP on a private network.

### 5.9 Verifying everything works

    # Ollama is up and has the right models
    curl -s http://127.0.0.1:11434/api/tags | grep -E "llama3.1|nomic-embed-text"

    # Web UI is up
    curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/

    # Manual pipeline run writes events
    sudo -u regwatch -H bash -c \
      'cd /opt/regwatch/app && . .venv/bin/activate && regwatch run-pipeline && regwatch dump-pipeline-runs --tail 3'

    # Timer is scheduled
    systemctl list-timers regwatch-pipeline.timer

    # Tail the app logs if something looks off
    journalctl -u regwatch-web -n 100 -f
    journalctl -u regwatch-pipeline.service -n 100

### 5.10 Backups

Everything the app knows about — regulations, events, versions, chunks, embeddings, chat sessions — is in `/opt/regwatch/data/app.db` plus the archived PDFs under `/opt/regwatch/data/pdfs/`. A nightly `tar` or `rsync` of `/opt/regwatch/data/` is all the backup you need. Because SQLite is in WAL mode, copy the `.db`, `.db-wal`, and `.db-shm` files together, or use `sqlite3 app.db ".backup '/path/to/backup.db'"` for a consistent snapshot while the app is running.

### 5.11 Updating to a newer version

    sudo systemctl stop regwatch-web regwatch-pipeline.timer
    sudo -u regwatch -H bash -c 'cd /opt/regwatch/app && git pull && . .venv/bin/activate && pip install -e ".[dev]"'
    # Run migrations if the DB schema changed (see alembic/ directory)
    sudo -u regwatch -H bash -c 'cd /opt/regwatch/app && . .venv/bin/activate && alembic upgrade head'
    sudo systemctl start regwatch-web regwatch-pipeline.timer

---

## 6. Troubleshooting in one screen

| Symptom | Likely cause | Fix |
|---|---|---|
| Dashboard loads but all KPIs are 0 and the inbox is empty | Pipeline has never run | Click "Run pipeline now", or wait for the systemd timer's first fire, or run `regwatch run-pipeline` manually |
| `httpx.HTTPStatusError: 404 ... /api/chat` | Configured chat model isn't pulled in Ollama | `ollama list` to see what's installed, then `ollama pull llama3.1:latest` and make sure `config.yaml` references the exact tag |
| "Pipeline run failed: OperationalError" flash | Two processes writing to the same SQLite file | Make sure only one uvicorn is running; check `systemctl status regwatch-web` and `ps aux | grep uvicorn` |
| Chat answers say "I could not find relevant information…" | No chunks indexed yet | Run `regwatch reindex` after a successful pipeline run, or make sure `nomic-embed-text` is pulled |
| Pipeline hangs for a long time | Ollama is doing reference extraction per document on CPU | Expected; a first full run on CPU can take 5–15 minutes. Watch `journalctl -u regwatch-pipeline -f` |
| `database is locked` in logs | A previous process left a dangling transaction | Restart `regwatch-web`: `sudo systemctl restart regwatch-web` |
| PDF flagged as protected in the Settings view | Encrypted PDF the extractor couldn't read | Download it manually, remove the password, upload it via the Settings page — the pipeline will re-index it |
