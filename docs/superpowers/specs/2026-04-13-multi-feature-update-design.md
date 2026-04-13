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
  # chat_model and embedding_model are optional in config —
  # if omitted, the first-startup flow prompts the user to select
  # from available models. Once selected, they are persisted in the
  # setting table and override config values.
  embedding_dim: 768
```

`OllamaConfig` → `LLMConfig` in `regwatch/config.py`. `chat_model` and `embedding_model` become `Optional[str]` with default `None` — the DB-persisted setting takes precedence, and if neither exists, the first-startup flow prompts the user.

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

## 2. ICT CSSF Circulars — LLM-Driven Discovery & User Overrides

### Problem

The ICT/DORA page and catalog show no CSSF circulars because none are marked `is_ict: true` in the seed. Hardcoding ICT classifications is fragile — circulars get superseded, applicability depends on authorization types, and manual curation doesn't scale. The tool must use the LLM to discover and classify relevant regulations, and the user must be able to correct mistakes.

### Design: LLM-driven regulation discovery

**No hardcoded ICT classifications.** The seed YAML provides the initial catalog structure but does NOT dictate `is_ict` or `dora_pillar` — those are determined dynamically by the LLM.

**Discovery flow** (triggered on first startup after model selection, and on-demand via a "Refresh catalog" button in the UI):

1. For each regulation in the catalog, send the title + URL + any available text to the LLM:
   ```
   System: You are a regulatory classification expert for Luxembourg financial entities.
   Given a regulation or circular, determine:
   1. is_ict: Is this related to ICT, cybersecurity, digital operational resilience, IT outsourcing, or similar technology risk topics? (true/false)
   2. dora_pillar: If is_ict is true, which DORA pillar? (ICT_RISK_MGMT, INCIDENT_REPORTING, RESILIENCE_TESTING, THIRD_PARTY_RISK, INFO_SHARING, or null)
   3. applicable_entity_types: Which entity types does this apply to? (JSON array of: "AIFM", "CHAPTER15_MANCO", "CREDIT_INSTITUTION", "CASP", "INVESTMENT_FIRM", "INSURANCE", "PENSION_FUND", or "ALL")
   4. is_superseded: Has this been replaced by a newer version? (true/false)
   5. superseded_by: If superseded, the reference number of the replacement (or null)

   Respond with ONLY a JSON object with these 5 fields.

   User: Classify this regulation:
   Reference: {reference_number}
   Title: {title}
   Issuing authority: {issuing_authority}
   Type: {type}
   ```
2. Parse the JSON response. Update the regulation record with `is_ict`, `dora_pillar`.
3. Skip regulations that have user overrides (see below).

**The LLM may also suggest new regulations** not yet in the catalog. A separate "Discover new regulations" step asks:
   ```
   System: You are a regulatory expert for Luxembourg. Given an entity with authorization types {auth_types}, list CSSF circulars and EU regulations relevant to ICT/DORA that may be missing from the following catalog. Only include currently applicable regulations (not superseded ones). Respond with a JSON array of objects: {reference_number, title, issuing_authority, type, is_ict, dora_pillar, url, applicability}.

   User: Current catalog:
   {list of reference_numbers and titles}
   ```
   New regulations discovered this way are added with `source_of_truth = "DISCOVERED"`.

### User override tracking

**New `regulation_override` table:**

```python
class RegulationOverride(Base):
    __tablename__ = "regulation_override"
    override_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int | None] = mapped_column(ForeignKey("regulation.regulation_id"), nullable=True)
    reference_number: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(20))  # INCLUDE / EXCLUDE / SET_ICT / UNSET_ICT
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
```

**Override semantics:**
- `EXCLUDE`: User removed this regulation — on re-discovery, skip it even if the LLM suggests it.
- `INCLUDE`: User added this regulation — on re-discovery, keep it even if the LLM doesn't find it.
- `SET_ICT`: User marked this as ICT — on re-discovery, preserve `is_ict=True`.
- `UNSET_ICT`: User marked this as non-ICT — on re-discovery, preserve `is_ict=False`.

During re-discovery, the tool checks overrides BEFORE applying LLM results. User decisions always win.

### Confidence and user review queue

The LLM classification prompt is extended to also return a `confidence` field (0.0–1.0). When confidence is below 0.7, the regulation is flagged as `needs_review = True` on the regulation record.

**New column on `regulation`:** `needs_review: Mapped[bool]` — `Boolean, default=False`.

**Review queue in the UI:** A "Pending review" section on the catalog/settings page shows regulations where `needs_review = True`. For each, the user can:
- Confirm the LLM's classification (clears `needs_review`, creates a confirming override)
- Override it (sets the correct values, creates an override, clears `needs_review`)

This ensures the tool never silently applies uncertain classifications. Items pending review are visible but marked with a warning indicator.

### UI for managing regulations

On the catalog page and ICT page, add:
- **"Add regulation"** button — form to manually add a reference number, title, type. Creates an `INCLUDE` override.
- **"Remove"** button per regulation — creates an `EXCLUDE` override and hides the regulation.
- **"Mark as ICT" / "Mark as non-ICT"** toggle per regulation — creates `SET_ICT` or `UNSET_ICT` override.
- **"Refresh catalog"** button — re-runs the LLM discovery, respecting all overrides.

### Pipeline ICT classification (for new incoming documents)

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

## 5. Settings — Model Selector, DB Persistence & First-Startup Flow

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

### First-startup flow

On first visit to the web UI, if no `chat_model` is persisted in the `setting` table:

1. **Redirect to `/settings/setup`** — a setup page that:
   - Auto-detects available models from the LLM server via `list_models()`
   - Presents a dropdown for chat model and embedding model
   - User selects and saves
2. **After model selection, trigger regulation discovery** — runs the LLM-driven catalog classification and discovery (Section 2). This populates `is_ict`, `dora_pillar`, and discovers missing regulations.
3. **Redirect to dashboard** — normal operation begins.

This only runs once. Subsequent startups load the persisted model from the `setting` table.

### Model selection flow

1. Settings page calls `LLMClient.list_models()` — tries `/v1/models` first (returns `data[].id`), fall back to `/api/tags` (returns `models[].name`).
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
| Regulation discovery | `regwatch/db/models.py` (new `RegulationOverride` model), `seeds/regulations_seed.yaml` (remove hardcoded `is_ict`) | `regwatch/services/discovery.py` | — |
| ICT classify | `regwatch/pipeline/match/classify.py` | — | — |
| Catalog UI | `regwatch/web/routes/catalog.py`, `regwatch/web/routes/ict.py`, catalog/ICT templates | — | — |
| Inbox | `regwatch/db/models.py`, `regwatch/services/inbox.py`, `regwatch/web/routes/inbox.py`, `regwatch/web/templates/inbox/list.html`, `regwatch/pipeline/persist.py` | — | — |
| Deadlines | `regwatch/db/models.py`, `regwatch/services/deadlines.py`, `regwatch/web/routes/deadlines.py`, `regwatch/web/templates/deadlines/list.html` | — | — |
| Settings | `regwatch/db/models.py`, `regwatch/web/routes/settings.py`, `regwatch/web/templates/settings.html` | `regwatch/services/settings.py`, `regwatch/web/templates/settings/setup.html` | — |
| Port | `config.example.yaml` | — | — |
| Tests | Various test files for new/changed functionality | — | — |

---

## Architectural notes for future work

The `applicable_entity_types` JSON column on `update_event` and the LLM-based classification are designed to be complemented by a future vector DB layer. When detailed document analysis is added later, the entity-type detection can be enhanced with embeddings-based classification rather than relying solely on the chat model. The JSON column format is flexible enough to accommodate additional entity types as the system evolves.

The `regulation_override` table provides a durable audit trail of all user corrections. This can later be used as training signal for improving the LLM prompts or for fine-tuning a classifier. The `needs_review` flag ensures human-in-the-loop validation when the tool is uncertain — critical for regulatory compliance where incorrect classification has real consequences.
