# Multi-Feature Update Design Spec

**Date:** 2026-04-13
**Scope:** Six changes — generic LLM backend, ICT CSSF circulars, inbox enhancements, deadline dismissal, settings model selector, port change.

---

## 1. Generic LLM Backend (LLM_BASE_URL)

### Problem

The codebase is hard-coded to Ollama's proprietary API (`/api/chat`, `/api/embed`, `/api/tags`). The user now runs LM Studio at `http://192.168.32.231:1234` and wants to switch freely between LLM servers.

### Design

**Config rename:** `ollama` → `llm` in both `AppConfig` and YAML:

```yaml
llm:
  base_url: "http://192.168.32.231:1234"
  chat_model: "llama3.1:latest"
  embedding_model: "nomic-embed-text"
  embedding_dim: 768
```

`OllamaConfig` → `LLMConfig` in `regwatch/config.py`.

**Client refactor:** Rename `OllamaClient` → `LLMClient` in `regwatch/llm/client.py` (move from `regwatch/ollama/`). The public interface stays identical: `chat()`, `chat_stream()`, `embed()`, `health()`.

**Auto-detection at health-check time:** `health()` probes the server to determine API format:
1. Try `GET /v1/models` — if it responds with a valid JSON body containing a `data` array, set `_api_format = "openai"`.
2. Otherwise try `GET /api/tags` — if it responds, set `_api_format = "ollama"`.
3. If neither works, `HealthStatus(reachable=False)`.

The detected format is cached on the client instance. All subsequent calls route to the correct endpoints:

| Method | Ollama format | OpenAI format |
|--------|--------------|---------------|
| `chat()` | `POST /api/chat` | `POST /v1/chat/completions` |
| `chat_stream()` | `POST /api/chat` (stream) | `POST /v1/chat/completions` (stream) |
| `embed()` | `POST /api/embed` | `POST /v1/embeddings` |
| `list_models()` | `GET /api/tags` | `GET /v1/models` |

Response parsing adapts per format (Ollama returns `message.content`, OpenAI returns `choices[0].message.content`, etc.).

**App state rename:** `app.state.ollama_client` → `app.state.llm_client`. All references updated across routes, services, tests.

**Error class:** `OllamaError` → `LLMError`. Existing catch clauses in the matcher that catch `OllamaError` updated.

**Module rename:** `regwatch/ollama/` → `regwatch/llm/`. Update all imports.

---

## 2. ICT CSSF Circulars — Seed Data & Pipeline Classification

### Problem

The ICT/DORA page and catalog show no CSSF circulars because none are marked `is_ict: true` in the seed. New documents fetched by the pipeline also aren't classified for ICT relevance beyond simple keywords.

### Seed data fix

Update `seeds/regulations_seed.yaml`:

| Circular | `is_ict` | `dora_pillar` |
|----------|----------|---------------|
| CSSF 18/698 (governance & security for IT outsourcing) | `true` | `THIRD_PARTY_RISK` |

CSSF 11/512 (risk management clarifications for UCITS ManCos), CSSF 23/844 (AIFM reporting under Art. 24 AIFMD), and CSSF 24/856 (NAV errors) are not ICT-related — they stay `is_ict: false`.

Add missing ICT-relevant circulars not currently in the seed:

| Circular | Title | `is_ict` | `dora_pillar` | Applicability |
|----------|-------|----------|---------------|---------------|
| CSSF 20/750 | Requirements on ICT risk management | `true` | `ICT_RISK_MGMT` | BOTH |
| CSSF 17/654 | IT outsourcing relying on a cloud computing infrastructure | `true` | `THIRD_PARTY_RISK` | BOTH |
| CSSF 22/806 | ICT-related incident reporting | `true` | `INCIDENT_REPORTING` | BOTH |

### Pipeline ICT classification

After the existing keyword-based `is_ict_document()` check, if it returns `False` and the LLM is available, use the chat model to classify:

```
System: You classify regulatory documents. Respond with ONLY "true" or "false".
User: Is this document related to ICT, cybersecurity, digital operational resilience, IT outsourcing, or similar technology risk topics?

Title: {title}
Text (first 2000 chars): {text[:2000]}
```

This runs in the classify phase of the pipeline (`regwatch/pipeline/match/classify.py`). The LLM call follows the same "latch off on error" pattern as `CombinedMatcher` — if the LLM is unreachable, fall back to keyword-only for the rest of the pipeline run.

---

## 3. Inbox Enhancements

### 3a. Entity-type filtering (automatic at pipeline time)

**New column on `update_event`:** `applicable_entity_types TEXT` — stores a JSON list of entity type strings (e.g., `["AIFM", "CHAPTER15_MANCO"]`, or `["CASP"]`, or `null` if unknown).

**Pipeline classify step:** After text extraction, call the LLM to determine target entity types:

```
System: You analyze regulatory documents to determine which types of financial entities they apply to. Respond with ONLY a JSON array of entity type strings. Common types: "AIFM" (Alternative Investment Fund Manager), "CHAPTER15_MANCO" (UCITS Management Company), "CASP" (Crypto-Asset Service Provider), "CREDIT_INSTITUTION", "INVESTMENT_FIRM", "INSURANCE", "PENSION_FUND". If the document applies broadly to all financial entities, respond with ["ALL"].

User: Which entity types does this document apply to?

Title: {title}
Text (first 2000 chars): {text[:2000]}
```

Parse the JSON response. On LLM error, store `null` (show the item anyway — don't hide it just because classification failed).

**Display-time filtering:** The inbox route defaults to showing only events where `applicable_entity_types` is `null` (unclassified) OR contains at least one of the entity's authorization types (AIFM, CHAPTER15_MANCO). A query parameter `?show_all=true` bypasses the filter.

### 3b. Description

**New column on `update_event`:** `description TEXT` (nullable).

**Population during persist:**
1. Check `raw_payload` for a `description` field (RSS feeds provide this). If present and non-empty, use it (truncated to 500 chars).
2. Otherwise, if the LLM is available, generate a 1-2 sentence summary:
   ```
   System: Summarize this regulatory document in 1-2 sentences for a compliance officer. Be concise.
   User: {title}\n\n{text[:2000]}
   ```
3. If neither available, leave `null`.

**Display:** Show the description under the title in the inbox list, in smaller/muted text.

### 3c. Source filter

**Source display name mapping** (in `regwatch/services/inbox.py` or a shared constant):

```python
SOURCE_DISPLAY_NAMES = {
    "cssf_rss": "CSSF",
    "cssf_consultation": "CSSF",
    "eurlex_cellar": "EUR-Lex",
    "eurlex_proposal": "EUR-Lex",
    "legilux_sparql": "Legilux",
    "legilux_parliamentary": "Legilux",
    "esma_rss": "ESMA",
    "eba_rss": "EBA",
    "ec_fisma_rss": "EC FISMA",
}
```

**Inbox route:** Accept `?source=CSSF` query parameter. Filter events where the source maps to the given display name. Add a dropdown filter in the template.

### 3d. Entity-type filter in inbox UI

Accept `?entity_type=AIFM` query parameter. Filter events where `applicable_entity_types` JSON contains the given type. Add a dropdown filter.

### 3e. DTO update

`UpdateEventDTO` gains: `description: str | None`, `applicable_entity_types: list[str] | None`, `source_display_name: str`.

---

## 4. Deadlines — Done / N/A

### Problem

Deadline items cannot be dismissed. Users see the same overdue items forever.

### Design

**New columns on `regulation`:**
- `transposition_done: Mapped[bool]` — `Boolean, default=False`
- `application_done: Mapped[bool]` — `Boolean, default=False`

**DeadlineService changes:**
- `upcoming()` filters out deadlines where the corresponding `_done` flag is `True` by default.
- New parameter `show_completed: bool = False` — when `True`, includes dismissed items (marked visually as done).
- New method: `set_done(regulation_id, kind, done)` — sets the flag and commits.

**Routes:**
- `POST /deadlines/{regulation_id}/dismiss` — body: `kind` (TRANSPOSITION/APPLICATION), `status` (done/na). Sets the corresponding `_done` flag to `True`.
- `POST /deadlines/{regulation_id}/restore` — sets the flag back to `False`.
- `GET /deadlines?show_completed=true` — toggle in UI.

**DeadlineDTO update:** Add `done: bool` field.

**UI:** Each deadline row gets a small button/dropdown to mark as "Done" or "N/A" (both set the `_done` flag to `True` — the distinction is cosmetic). A "Show completed" checkbox at the top toggles visibility.

---

## 5. Settings — Model Selector & DB Persistence

### New `setting` table

```python
class Setting(Base):
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
```

### Settings service

`regwatch/services/settings.py`:
- `get(key, default=None) -> str | None`
- `set(key, value) -> None`
- `get_all() -> dict[str, str]`

### Model selection flow

1. Settings page calls `LLMClient.list_models()` — tries `/v1/models` first (returns `data[].id`), falls back to `/api/tags` (returns `models[].name`).
2. Display models in a dropdown for both chat and embedding model.
3. On save, persist `chat_model` and `embedding_model` to the `setting` table.
4. On app startup (in `create_app`), load settings from DB and use them to override config defaults when constructing `LLMClient`.
5. The settings page also shows the current `LLM_BASE_URL` (read-only from config).

### Settings page updates

The settings page currently shows Ollama health, protected PDFs, and pipeline runs. Add a "Model Configuration" section at the top with:
- Current LLM server URL (read-only)
- Chat model dropdown + save button
- Embedding model dropdown + save button
- Health status indicator

---

## 6. Port Change

Update `config.example.yaml`: `port: 8001`.

The user's `config.yaml` (gitignored) also needs to be updated, but that's a runtime concern. The app reads `config.ui.port` — once the example is changed and the user regenerates their config, the port changes.

---

## File change summary

| Area | Files modified | Files created | Files deleted |
|------|---------------|---------------|---------------|
| LLM client | `regwatch/config.py`, `regwatch/main.py`, all routes/services referencing `ollama_client` | `regwatch/llm/client.py`, `regwatch/llm/__init__.py` | `regwatch/ollama/client.py`, `regwatch/ollama/__init__.py` |
| ICT seed | `seeds/regulations_seed.yaml` | — | — |
| ICT classify | `regwatch/pipeline/match/classify.py` | — | — |
| Inbox | `regwatch/db/models.py`, `regwatch/services/inbox.py`, `regwatch/web/routes/inbox.py`, `regwatch/web/templates/inbox/list.html`, `regwatch/pipeline/persist.py` | — | — |
| Deadlines | `regwatch/db/models.py`, `regwatch/services/deadlines.py`, `regwatch/web/routes/deadlines.py`, `regwatch/web/templates/deadlines/list.html` | — | — |
| Settings | `regwatch/db/models.py`, `regwatch/web/routes/settings.py`, `regwatch/web/templates/settings.html` | `regwatch/services/settings.py` | — |
| Port | `config.example.yaml` | — | — |
| Tests | Various test files for new/changed functionality | — | — |

---

## Architectural notes for future work

The `applicable_entity_types` JSON column on `update_event` and the LLM-based classification are designed to be complemented by a future vector DB layer. When detailed document analysis is added later, the entity-type detection can be enhanced with embeddings-based classification rather than relying solely on the chat model. The JSON column format is flexible enough to accommodate additional entity types as the system evolves.
