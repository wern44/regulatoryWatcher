# Multi-Feature Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Ollama-specific backend with generic LLM client, add LLM-driven regulation discovery with user overrides, enhance inbox with entity-type filtering/descriptions/source filters, add deadline dismissal, add model selector in settings, and change default port to 8001.

**Architecture:** The LLM client is refactored from Ollama-specific to a dual-format client (OpenAI + Ollama APIs) with auto-detection. A new discovery service uses the LLM to classify regulations for ICT relevance and entity-type applicability. User corrections are tracked via a `regulation_override` table that takes precedence over LLM results on re-discovery. A `setting` table persists model selection and other runtime preferences.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Jinja2/HTMX, httpx, Pydantic, pytest, pytest-httpx

**Spec:** `docs/superpowers/specs/2026-04-13-multi-feature-update-design.md`

---

## File Structure

### New files
- `regwatch/llm/__init__.py` — package init
- `regwatch/llm/client.py` — generic LLM client with OpenAI + Ollama auto-detection
- `regwatch/services/settings.py` — settings service (DB-backed key-value)
- `regwatch/services/discovery.py` — LLM-driven regulation discovery + classification
- `regwatch/web/templates/settings/setup.html` — first-startup model selection page
- `tests/unit/test_llm_client.py` — LLM client tests
- `tests/unit/test_settings_service.py` — settings service tests
- `tests/unit/test_discovery_service.py` — discovery service tests

### Modified files
- `regwatch/config.py` — rename `OllamaConfig` → `LLMConfig`, make models optional
- `config.example.yaml` — rename `ollama` → `llm`, change port to 8001
- `regwatch/main.py` — use `LLMClient`, load DB settings, first-startup redirect
- `regwatch/cli.py` — update all `OllamaClient` → `LLMClient` imports
- `regwatch/db/models.py` — add `Setting`, `RegulationOverride`, new columns on `Regulation` and `UpdateEvent`
- `regwatch/db/seed.py` — stop overwriting `is_ict` if set by discovery
- `regwatch/pipeline/match/combined.py` — `OllamaClient` → `LLMClient`
- `regwatch/pipeline/match/ollama_refs.py` — `OllamaClient` → `LLMClient`
- `regwatch/pipeline/match/classify.py` — add LLM-based ICT classification
- `regwatch/pipeline/pipeline_factory.py` — `OllamaClient` → `LLMClient`, add entity-type + description classification
- `regwatch/pipeline/persist.py` — persist `description` and `applicable_entity_types`
- `regwatch/rag/retrieval.py` — `OllamaClient` → `LLMClient`
- `regwatch/rag/chat_service.py` — `OllamaClient` → `LLMClient`
- `regwatch/rag/answer.py` — `OllamaClient` → `LLMClient`
- `regwatch/rag/indexing.py` — `OllamaClient` → `LLMClient`
- `regwatch/services/inbox.py` — add filtering, description, source display name
- `regwatch/services/deadlines.py` — add `done` filtering and `set_done`
- `regwatch/services/regulations.py` — add `needs_review` to DTO
- `regwatch/web/routes/inbox.py` — add query params for source, entity_type, show_all
- `regwatch/web/routes/deadlines.py` — add dismiss/restore routes, show_completed param
- `regwatch/web/routes/settings.py` — add model selector, setup flow, discovery trigger
- `regwatch/web/routes/ict.py` — add manage buttons (mark ICT/non-ICT, remove)
- `regwatch/web/routes/catalog.py` — add manage buttons, add regulation form, refresh
- `regwatch/web/routes/actions.py` — `ollama_client` → `llm_client`
- `regwatch/web/routes/chat.py` — `ollama_client` → `llm_client`
- `regwatch/web/templates/settings.html` — model selector UI, LLM section
- `regwatch/web/templates/inbox/list.html` — filter dropdowns, description display
- `regwatch/web/templates/partials/inbox_row.html` — description, entity types, source display name
- `regwatch/web/templates/deadlines/list.html` — done/na buttons, show completed toggle
- `regwatch/web/templates/ict/list.html` — manage buttons, needs_review indicator
- `seeds/regulations_seed.yaml` — remove hardcoded `is_ict` values (set all to false)

### Deleted files
- `regwatch/ollama/__init__.py`
- `regwatch/ollama/client.py`

---

## Task 1: Generic LLM Client — Core Module

**Files:**
- Create: `regwatch/llm/__init__.py`
- Create: `regwatch/llm/client.py`
- Create: `tests/unit/test_llm_client.py`

- [ ] **Step 1: Write failing tests for the LLM client**

Create `tests/unit/test_llm_client.py`:

```python
"""Tests for the generic LLM client with OpenAI + Ollama auto-detection."""
from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from regwatch.llm.client import LLMClient, LLMError


class TestOllamaFormat:
    """Tests when the server speaks Ollama's native API."""

    def test_chat_returns_content(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:11434/v1/models",
            status_code=404,
        )
        httpx_mock.add_response(
            url="http://localhost:11434/api/tags",
            json={"models": [{"name": "llama3.1:8b"}]},
        )
        httpx_mock.add_response(
            url="http://localhost:11434/api/chat",
            json={"message": {"role": "assistant", "content": "Hello!"}, "done": True},
        )
        client = LLMClient(
            base_url="http://localhost:11434",
            chat_model="llama3.1:8b",
            embedding_model="nomic-embed-text",
        )
        client.health()  # triggers auto-detection
        reply = client.chat(system="sys", user="hi")
        assert reply == "Hello!"

    def test_embed_returns_vector(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:11434/v1/models",
            status_code=404,
        )
        httpx_mock.add_response(
            url="http://localhost:11434/api/tags",
            json={"models": [{"name": "nomic-embed-text"}]},
        )
        httpx_mock.add_response(
            url="http://localhost:11434/api/embed",
            json={"embeddings": [[0.1, 0.2, 0.3]]},
        )
        client = LLMClient(
            base_url="http://localhost:11434",
            chat_model="x",
            embedding_model="nomic-embed-text",
        )
        client.health()
        vector = client.embed("some text")
        assert vector == [0.1, 0.2, 0.3]

    def test_health_check_ollama(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:11434/v1/models",
            status_code=404,
        )
        httpx_mock.add_response(
            url="http://localhost:11434/api/tags",
            json={"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text"}]},
        )
        client = LLMClient(
            base_url="http://localhost:11434",
            chat_model="llama3.1:8b",
            embedding_model="nomic-embed-text",
        )
        status = client.health()
        assert status.reachable is True
        assert status.chat_model_available is True
        assert status.embedding_model_available is True

    def test_list_models_ollama(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:11434/v1/models",
            status_code=404,
        )
        httpx_mock.add_response(
            url="http://localhost:11434/api/tags",
            json={"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text"}]},
        )
        client = LLMClient(
            base_url="http://localhost:11434",
            chat_model="llama3.1:8b",
            embedding_model="nomic-embed-text",
        )
        models = client.list_models()
        assert "llama3.1:8b" in models
        assert "nomic-embed-text" in models


class TestOpenAIFormat:
    """Tests when the server speaks the OpenAI-compatible API (LM Studio, etc.)."""

    def test_chat_returns_content(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://192.168.32.231:1234/v1/models",
            json={"data": [{"id": "google/gemma-4-26b-a4b"}]},
        )
        httpx_mock.add_response(
            url="http://192.168.32.231:1234/v1/chat/completions",
            json={
                "choices": [{"message": {"role": "assistant", "content": "Hi there!"}}],
            },
        )
        client = LLMClient(
            base_url="http://192.168.32.231:1234",
            chat_model="google/gemma-4-26b-a4b",
            embedding_model="text-embedding-nomic-embed-text-v1.5",
        )
        client.health()
        reply = client.chat(system="sys", user="hello")
        assert reply == "Hi there!"

    def test_embed_returns_vector(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://192.168.32.231:1234/v1/models",
            json={"data": [{"id": "text-embedding-nomic-embed-text-v1.5"}]},
        )
        httpx_mock.add_response(
            url="http://192.168.32.231:1234/v1/embeddings",
            json={"data": [{"embedding": [0.4, 0.5, 0.6]}]},
        )
        client = LLMClient(
            base_url="http://192.168.32.231:1234",
            chat_model="x",
            embedding_model="text-embedding-nomic-embed-text-v1.5",
        )
        client.health()
        vector = client.embed("some text")
        assert vector == [0.4, 0.5, 0.6]

    def test_health_check_openai(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://192.168.32.231:1234/v1/models",
            json={
                "data": [
                    {"id": "google/gemma-4-26b-a4b"},
                    {"id": "text-embedding-nomic-embed-text-v1.5"},
                ]
            },
        )
        client = LLMClient(
            base_url="http://192.168.32.231:1234",
            chat_model="google/gemma-4-26b-a4b",
            embedding_model="text-embedding-nomic-embed-text-v1.5",
        )
        status = client.health()
        assert status.reachable is True
        assert status.chat_model_available is True
        assert status.embedding_model_available is True

    def test_list_models_openai(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://192.168.32.231:1234/v1/models",
            json={
                "data": [
                    {"id": "google/gemma-4-26b-a4b"},
                    {"id": "text-embedding-nomic-embed-text-v1.5"},
                ]
            },
        )
        client = LLMClient(
            base_url="http://192.168.32.231:1234",
            chat_model="google/gemma-4-26b-a4b",
            embedding_model="text-embedding-nomic-embed-text-v1.5",
        )
        models = client.list_models()
        assert "google/gemma-4-26b-a4b" in models


class TestUnreachable:
    def test_health_unreachable(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("refused"))
        client = LLMClient(
            base_url="http://localhost:11434",
            chat_model="x",
            embedding_model="y",
        )
        status = client.health()
        assert status.reachable is False

    def test_chat_before_detection_uses_openai_by_default(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="http://localhost:1234/v1/chat/completions",
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )
        client = LLMClient(
            base_url="http://localhost:1234",
            chat_model="m",
            embedding_model="e",
        )
        # No health() call — should default to openai format
        reply = client.chat(system="s", user="u")
        assert reply == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_llm_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regwatch.llm'`

- [ ] **Step 3: Create the LLM client package**

Create `regwatch/llm/__init__.py`:

```python
```

Create `regwatch/llm/client.py`:

```python
"""Generic LLM client with auto-detection for OpenAI-compatible and Ollama APIs."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

ApiFormat = Literal["openai", "ollama"]


class LLMError(RuntimeError):
    pass


@dataclass
class HealthStatus:
    reachable: bool
    chat_model_available: bool = False
    embedding_model_available: bool = False


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._chat_model = chat_model
        self._embedding_model = embedding_model
        self._timeout = timeout
        self._api_format: ApiFormat = "openai"  # default until detected
        self._detected = False

    @property
    def chat_model(self) -> str:
        return self._chat_model

    @chat_model.setter
    def chat_model(self, value: str) -> None:
        self._chat_model = value

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    @embedding_model.setter
    def embedding_model(self, value: str) -> None:
        self._embedding_model = value

    def _detect_api_format(self) -> tuple[ApiFormat, list[str]]:
        """Probe the server to determine which API format it speaks.

        Returns (format, model_names). Tries OpenAI first, then Ollama.
        """
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self._base_url}/v1/models")
                r.raise_for_status()
                data = r.json()
                if isinstance(data.get("data"), list):
                    names = [m.get("id", "") for m in data["data"]]
                    return "openai", names
        except Exception:  # noqa: BLE001
            pass

        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self._base_url}/api/tags")
                r.raise_for_status()
                data = r.json()
                names = [m.get("name", "") for m in data.get("models", [])]
                return "ollama", names
        except Exception:  # noqa: BLE001
            pass

        return "openai", []

    def health(self) -> HealthStatus:
        try:
            fmt, names = self._detect_api_format()
        except Exception:  # noqa: BLE001
            return HealthStatus(reachable=False)

        if not names and fmt == "openai":
            return HealthStatus(reachable=False)

        self._api_format = fmt
        self._detected = True

        name_set = set(names)
        chat_ok = self._chat_model in name_set or any(
            n.startswith(self._chat_model.split(":")[0]) for n in name_set
        )
        embed_ok = self._embedding_model in name_set or any(
            n.startswith(self._embedding_model.split(":")[0]) for n in name_set
        )
        return HealthStatus(
            reachable=True,
            chat_model_available=chat_ok,
            embedding_model_available=embed_ok,
        )

    def list_models(self) -> list[str]:
        """Return a list of available model names/ids from the server."""
        _, names = self._detect_api_format()
        return names

    def chat(self, *, system: str, user: str) -> str:
        if self._api_format == "openai":
            return self._chat_openai(system, user)
        return self._chat_ollama(system, user)

    def _chat_openai(self, system: str, user: str) -> str:
        payload = {
            "model": self._chat_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(f"{self._base_url}/v1/chat/completions", json=payload)
            r.raise_for_status()
            data = r.json()
        choices = data.get("choices", [])
        if not choices:
            raise LLMError("Empty response from chat endpoint")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise LLMError("Empty response from chat endpoint")
        return content

    def _chat_ollama(self, system: str, user: str) -> str:
        payload = {
            "model": self._chat_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(f"{self._base_url}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError("Empty response from chat endpoint")
        return content

    def chat_stream(self, *, system: str, user: str) -> Iterator[str]:
        """Yield content chunks from a streaming chat response."""
        if self._api_format == "openai":
            yield from self._chat_stream_openai(system, user)
        else:
            yield from self._chat_stream_ollama(system, user)

    def _chat_stream_openai(self, system: str, user: str) -> Iterator[str]:
        payload = {
            "model": self._chat_model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._timeout) as client:
            with client.stream(
                "POST", f"{self._base_url}/v1/chat/completions", json=payload
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload_str = line[len("data: "):]
                    if payload_str.strip() == "[DONE]":
                        return
                    data = json.loads(payload_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    chunk = delta.get("content")
                    if chunk:
                        yield chunk

    def _chat_stream_ollama(self, system: str, user: str) -> Iterator[str]:
        payload = {
            "model": self._chat_model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._timeout) as client:
            with client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    chunk = data.get("message", {}).get("content")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        return

    def embed(self, text: str) -> list[float]:
        if self._api_format == "openai":
            return self._embed_openai(text)
        return self._embed_ollama(text)

    def _embed_openai(self, text: str) -> list[float]:
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(
                f"{self._base_url}/v1/embeddings",
                json={"model": self._embedding_model, "input": text},
            )
            r.raise_for_status()
            data = r.json()
        items = data.get("data", [])
        if not items:
            raise LLMError("Empty embeddings response")
        return list(items[0]["embedding"])

    def _embed_ollama(self, text: str) -> list[float]:
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._embedding_model, "input": text},
            )
            r.raise_for_status()
            data = r.json()
        vectors = data.get("embeddings", [])
        if not vectors:
            raise LLMError("Empty embeddings response")
        return list(vectors[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_llm_client.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add regwatch/llm/__init__.py regwatch/llm/client.py tests/unit/test_llm_client.py
git commit -m "feat: add generic LLM client with OpenAI + Ollama auto-detection"
```

---

## Task 2: Config Rename — `ollama` to `llm`

**Files:**
- Modify: `regwatch/config.py`
- Modify: `config.example.yaml`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Update config.py**

In `regwatch/config.py`, make these changes:

Rename `OllamaConfig` → `LLMConfig` and make models optional:

```python
class LLMConfig(BaseModel):
    base_url: str
    chat_model: str | None = None
    embedding_model: str | None = None
    embedding_dim: int = 768
```

In `AppConfig`, rename the field:

```python
class AppConfig(BaseModel):
    entity: EntityConfig
    sources: dict[str, SourceConfig]
    llm: LLMConfig
    rag: RagConfig
    paths: PathsConfig
    ui: UiConfig
```

- [ ] **Step 2: Update config.example.yaml**

Replace the `ollama:` section with `llm:` and change port:

```yaml
llm:
  base_url: "http://192.168.32.231:1234"
  embedding_dim: 768

ui:
  language: en
  timezone: "Europe/Luxembourg"
  host: "127.0.0.1"
  port: 8001
```

- [ ] **Step 3: Update test_config.py if it references `ollama`**

Check and update any test that references `config.ollama` to `config.llm`.

- [ ] **Step 4: Run config tests**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add regwatch/config.py config.example.yaml tests/unit/test_config.py
git commit -m "refactor: rename OllamaConfig to LLMConfig, port to 8001"
```

---

## Task 3: Rename All Ollama References Codebase-Wide

**Files:**
- Modify: `regwatch/main.py`
- Modify: `regwatch/cli.py`
- Modify: `regwatch/pipeline/pipeline_factory.py`
- Modify: `regwatch/pipeline/match/combined.py`
- Modify: `regwatch/pipeline/match/ollama_refs.py`
- Modify: `regwatch/rag/retrieval.py`
- Modify: `regwatch/rag/chat_service.py`
- Modify: `regwatch/rag/answer.py`
- Modify: `regwatch/rag/indexing.py`
- Modify: `regwatch/web/routes/actions.py`
- Modify: `regwatch/web/routes/chat.py`
- Modify: `regwatch/web/routes/settings.py`
- Modify: `regwatch/web/templates/settings.html`
- Delete: `regwatch/ollama/client.py`
- Delete: `regwatch/ollama/__init__.py`

- [ ] **Step 1: Update all imports and references**

Every file that imports from `regwatch.ollama.client` must change to `regwatch.llm.client`. Every reference to `OllamaClient` → `LLMClient`, `OllamaError` → `LLMError`, `app.state.ollama_client` → `app.state.llm_client`, `config.ollama` → `config.llm`.

**`regwatch/main.py`:**
- Line 18: `from regwatch.ollama.client import OllamaClient` → `from regwatch.llm.client import LLMClient`
- Line 32: `create_virtual_tables(engine, embedding_dim=config.ollama.embedding_dim)` → `config.llm.embedding_dim`
- Lines 54-58: Replace `OllamaClient(...)` with `LLMClient(...)`, using `config.llm.*`. Handle optional models by checking DB settings first.
- Line 54: `app.state.ollama_client` → `app.state.llm_client`

**`regwatch/cli.py`:**
- Lines 55, 162, 191, 234: `cfg.ollama.*` → `cfg.llm.*`
- Lines 162, 191, 234: `from regwatch.ollama.client import OllamaClient` → `from regwatch.llm.client import LLMClient`
- Lines 168, 194, 238: `OllamaClient(` → `LLMClient(`

**`regwatch/pipeline/pipeline_factory.py`:**
- Line 15: `from regwatch.ollama.client import OllamaClient` → `from regwatch.llm.client import LLMClient`
- Line 29: `ollama_client: OllamaClient | None = None` → `ollama_client: LLMClient | None = None`

**`regwatch/pipeline/match/combined.py`:**
- Line 10: `from regwatch.ollama.client import OllamaClient, OllamaError` → `from regwatch.llm.client import LLMClient, LLMError`
- Line 19: `ollama: OllamaClient | None = None` → `ollama: LLMClient | None = None`
- Line 39: `except (httpx.HTTPError, OllamaError)` → `except (httpx.HTTPError, LLMError)`

**`regwatch/pipeline/match/ollama_refs.py`:**
- Line 7: `from regwatch.ollama.client import OllamaClient` → `from regwatch.llm.client import LLMClient`
- Line 22: `def extract_references(client: OllamaClient,` → `def extract_references(client: LLMClient,`

**`regwatch/rag/retrieval.py`:**
- Line 18: `from regwatch.ollama.client import OllamaClient` → `from regwatch.llm.client import LLMClient`
- Line 42: `ollama: OllamaClient` → `ollama: LLMClient`

**`regwatch/rag/chat_service.py`:**
- Line 10: `from regwatch.ollama.client import OllamaClient` → `from regwatch.llm.client import LLMClient`
- Line 17: `ollama: OllamaClient` → `ollama: LLMClient`

**`regwatch/rag/answer.py`:**
- Line 6: `from regwatch.ollama.client import OllamaClient` → `from regwatch.llm.client import LLMClient`
- Line 31: `ollama: OllamaClient` → `ollama: LLMClient`

**`regwatch/rag/indexing.py`:**
- Line 11: `from regwatch.ollama.client import OllamaClient` → `from regwatch.llm.client import LLMClient`
- Line 19: `ollama: OllamaClient` → `ollama: LLMClient`

**`regwatch/web/routes/actions.py`:**
- Line 85: `"ollama_client": request.app.state.ollama_client` → `"llm_client": request.app.state.llm_client`
- Line 24: `ollama_client,` → `llm_client,`
- Line 41: `ollama_client=ollama_client` → `ollama_client=llm_client`

**`regwatch/web/routes/chat.py`:**
- Lines 32, 48, 65: `request.app.state.ollama_client` → `request.app.state.llm_client`

**`regwatch/web/routes/settings.py`:**
- Line 12: `from regwatch.ollama.client import HealthStatus` → `from regwatch.llm.client import HealthStatus`
- Line 26: `request.app.state.ollama_client` → `request.app.state.llm_client`

**`regwatch/web/templates/settings.html`:**
- Line 63: `Ollama` → `LLM Server`
- Line 65: `config.ollama.base_url` → `config.llm.base_url`
- Line 66: `config.ollama.chat_model` → references to current model from settings
- Line 67: `config.ollama.embedding_model` → references to current model from settings
- Lines 70-74: `ollama_health` stays as context variable name

- [ ] **Step 2: Delete old Ollama package**

Delete `regwatch/ollama/client.py` and `regwatch/ollama/__init__.py`.

- [ ] **Step 3: Update test files that reference Ollama**

Update `tests/unit/test_ollama_client.py` → rename to `tests/unit/test_llm_client.py` (already created, so delete the old one).

Update `tests/unit/test_ollama_refs.py`, `tests/unit/test_combined_matcher.py`, and all integration tests that reference `ollama_client` to use `llm_client`.

- [ ] **Step 4: Run full test suite**

Run: `pytest -x`
Expected: all existing tests PASS (with updated imports)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: rename OllamaClient to LLMClient across entire codebase"
```

---

## Task 4: Database Models — New Tables and Columns

**Files:**
- Modify: `regwatch/db/models.py`

- [ ] **Step 1: Add Setting model**

After the `PipelineRun` class in `regwatch/db/models.py`, add:

```python
class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
```

- [ ] **Step 2: Add RegulationOverride model**

After the `RegulationLifecycleLink` class, add:

```python
class RegulationOverride(Base):
    __tablename__ = "regulation_override"

    override_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int | None] = mapped_column(
        ForeignKey("regulation.regulation_id"), nullable=True
    )
    reference_number: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(20))  # INCLUDE / EXCLUDE / SET_ICT / UNSET_ICT
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
```

- [ ] **Step 3: Add new columns to Regulation**

Add after the existing `notes` column on `Regulation`:

```python
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    transposition_done: Mapped[bool] = mapped_column(Boolean, default=False)
    application_done: Mapped[bool] = mapped_column(Boolean, default=False)
```

- [ ] **Step 4: Add new columns to UpdateEvent**

Add after the existing `notes` column on `UpdateEvent`:

```python
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    applicable_entity_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_db_models.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add regwatch/db/models.py
git commit -m "feat(db): add Setting, RegulationOverride models and new columns"
```

---

## Task 5: Settings Service

**Files:**
- Create: `regwatch/services/settings.py`
- Create: `tests/unit/test_settings_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_settings_service.py`:

```python
"""Tests for the settings service."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from regwatch.db.models import Base, Setting
from regwatch.services.settings import SettingsService


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)()


def test_get_returns_none_when_missing(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    assert svc.get("nonexistent") is None


def test_get_returns_default_when_missing(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    assert svc.get("nonexistent", "fallback") == "fallback"


def test_set_and_get(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    svc.set("chat_model", "google/gemma-4-26b-a4b")
    session.commit()
    assert svc.get("chat_model") == "google/gemma-4-26b-a4b"


def test_set_overwrites_existing(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    svc.set("chat_model", "old")
    session.commit()
    svc.set("chat_model", "new")
    session.commit()
    assert svc.get("chat_model") == "new"


def test_get_all(tmp_path: Path) -> None:
    session = _session(tmp_path)
    svc = SettingsService(session)
    svc.set("a", "1")
    svc.set("b", "2")
    session.commit()
    result = svc.get_all()
    assert result == {"a": "1", "b": "2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_settings_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the settings service**

Create `regwatch/services/settings.py`:

```python
"""Settings service: DB-backed key-value store for runtime configuration."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from regwatch.db.models import Setting


class SettingsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._session.get(Setting, key)
        return row.value if row is not None else default

    def set(self, key: str, value: str) -> None:
        row = self._session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value, updated_at=datetime.now(UTC))
            self._session.add(row)
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)

    def get_all(self) -> dict[str, str]:
        rows = self._session.query(Setting).all()
        return {r.key: r.value for r in rows}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_settings_service.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add regwatch/services/settings.py tests/unit/test_settings_service.py
git commit -m "feat: add settings service for DB-backed configuration"
```

---

## Task 6: App Startup — Load Settings from DB, First-Startup Redirect

**Files:**
- Modify: `regwatch/main.py`

- [ ] **Step 1: Update create_app to load settings from DB**

In `regwatch/main.py`, after creating the session_factory but before creating the LLMClient, load persisted settings:

```python
from regwatch.llm.client import LLMClient
from regwatch.services.settings import SettingsService

def create_app() -> FastAPI:
    config_path = Path(os.environ.get("REGWATCH_CONFIG", "config.yaml"))
    config = load_config(config_path)

    engine = create_app_engine(config.paths.db_file)
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=config.llm.embedding_dim)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    # Load persisted model settings from DB, fall back to config
    with session_factory() as session:
        settings_svc = SettingsService(session)
        chat_model = settings_svc.get("chat_model") or config.llm.chat_model or ""
        embedding_model = settings_svc.get("embedding_model") or config.llm.embedding_model or ""

    # ... rest of app setup ...

    app.state.llm_client = LLMClient(
        base_url=config.llm.base_url,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )
```

- [ ] **Step 2: Add first-startup middleware**

Add a middleware that redirects to `/settings/setup` if no chat_model is configured:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse as StarletteRedirect

class FirstStartupMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Allow static files and the setup page itself
        path = request.url.path
        if path.startswith("/static") or path.startswith("/settings"):
            return await call_next(request)
        if not request.app.state.llm_client.chat_model:
            return StarletteRedirect(url="/settings/setup")
        return await call_next(request)

# In create_app(), after creating the app:
app.add_middleware(FirstStartupMiddleware)
```

- [ ] **Step 3: Run smoke test**

Run: `pytest tests/integration/test_app_smoke.py -v`
Expected: PASS (the example config provides models, so no redirect)

- [ ] **Step 4: Commit**

```bash
git add regwatch/main.py
git commit -m "feat: load model settings from DB, add first-startup redirect"
```

---

## Task 7: Settings Page — Model Selector UI

**Files:**
- Modify: `regwatch/web/routes/settings.py`
- Modify: `regwatch/web/templates/settings.html`
- Create: `regwatch/web/templates/settings/setup.html`

- [ ] **Step 1: Add model listing and saving routes**

In `regwatch/web/routes/settings.py`, add:

```python
from regwatch.services.settings import SettingsService

@router.get("/setup", response_class=HTMLResponse)
def setup_view(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    llm = request.app.state.llm_client
    models = llm.list_models()
    return templates.TemplateResponse(
        request,
        "settings/setup.html",
        {"models": models},
    )


@router.post("/setup", response_class=HTMLResponse)
def setup_save(
    request: Request,
    chat_model: str = Form(...),
    embedding_model: str = Form(...),
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        svc.set("chat_model", chat_model)
        svc.set("embedding_model", embedding_model)
        session.commit()
    # Update the live client
    request.app.state.llm_client.chat_model = chat_model
    request.app.state.llm_client.embedding_model = embedding_model
    return RedirectResponse(url="/", status_code=303)


@router.post("/save-models")
def save_models(
    request: Request,
    chat_model: str = Form(...),
    embedding_model: str = Form(...),
) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        svc = SettingsService(session)
        svc.set("chat_model", chat_model)
        svc.set("embedding_model", embedding_model)
        session.commit()
    request.app.state.llm_client.chat_model = chat_model
    request.app.state.llm_client.embedding_model = embedding_model
    return RedirectResponse(url="/settings", status_code=303)
```

- [ ] **Step 2: Create the setup template**

Create `regwatch/web/templates/settings/setup.html`:

```html
{% extends "base.html" %}
{% block title %}RegWatch — Initial Setup{% endblock %}
{% block content %}
  <div class="max-w-lg mx-auto mt-12">
    <h1 class="text-2xl font-bold mb-2">Welcome to RegWatch</h1>
    <p class="text-slate-600 mb-6">Select the LLM models to use. These can be changed later in Settings.</p>

    <form method="post" action="/settings/setup" class="bg-white p-6 rounded shadow-sm border space-y-4">
      <div>
        <label class="block text-sm font-medium mb-1">Chat Model</label>
        <select name="chat_model" required class="w-full border rounded px-3 py-2 text-sm">
          {% for m in models %}
          <option value="{{ m }}">{{ m }}</option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">Embedding Model</label>
        <select name="embedding_model" required class="w-full border rounded px-3 py-2 text-sm">
          {% for m in models %}
          <option value="{{ m }}">{{ m }}</option>
          {% endfor %}
        </select>
      </div>
      <button type="submit" class="w-full px-4 py-2 bg-slate-800 text-white rounded hover:bg-slate-700">
        Save &amp; Continue
      </button>
    </form>
  </div>
{% endblock %}
```

- [ ] **Step 3: Update the settings template with LLM section**

Replace the `Ollama` section in `regwatch/web/templates/settings.html` with a model configuration section that shows dropdowns populated by available models, the current selections, and a save button. Replace references to `config.ollama` with `config.llm` and current model names from context.

```html
  <section class="bg-white p-4 rounded shadow-sm border mb-4">
    <h2 class="text-lg font-semibold mb-2">LLM Server</h2>
    <div class="text-sm space-y-1 mb-3">
      <div><strong>Base URL:</strong> {{ config.llm.base_url }}</div>
      <div>
        <strong>Status:</strong>
        {% if llm_health.reachable %}
          <span class="text-green-700">reachable</span>
        {% else %}
          <span class="text-red-700">unreachable</span>
        {% endif %}
      </div>
    </div>
    <form method="post" action="/settings/save-models" class="space-y-3">
      <div>
        <label class="block text-xs font-medium mb-1">Chat Model</label>
        <select name="chat_model" class="w-full border rounded px-3 py-2 text-sm">
          {% for m in available_models %}
          <option value="{{ m }}" {% if m == current_chat_model %}selected{% endif %}>{{ m }}</option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label class="block text-xs font-medium mb-1">Embedding Model</label>
        <select name="embedding_model" class="w-full border rounded px-3 py-2 text-sm">
          {% for m in available_models %}
          <option value="{{ m }}" {% if m == current_embedding_model %}selected{% endif %}>{{ m }}</option>
          {% endfor %}
        </select>
      </div>
      <button type="submit" class="px-3 py-2 bg-slate-800 text-white rounded text-sm hover:bg-slate-700">
        Save model selection
      </button>
    </form>
  </section>
```

- [ ] **Step 4: Update settings_view route to pass model data**

In the `settings_view` function, add model listing and current model context:

```python
    llm = request.app.state.llm_client
    try:
        llm_health = llm.health()
    except Exception:
        llm_health = HealthStatus(reachable=False)
    available_models = llm.list_models()

    # ... existing code ...

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "config": config,
            "llm_health": llm_health,
            "available_models": available_models,
            "current_chat_model": llm.chat_model,
            "current_embedding_model": llm.embedding_model,
            "protected_versions": protected,
            "runs": runs,
            "db_action": db_action,
            "db_error": db_error,
        },
    )
```

- [ ] **Step 5: Run settings tests**

Run: `pytest tests/integration/test_settings_view.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add regwatch/web/routes/settings.py regwatch/web/templates/settings.html regwatch/web/templates/settings/setup.html
git commit -m "feat: add model selector UI and first-startup setup page"
```

---

## Task 8: Deadline Dismissal

**Files:**
- Modify: `regwatch/services/deadlines.py`
- Modify: `regwatch/web/routes/deadlines.py`
- Modify: `regwatch/web/templates/deadlines/list.html`
- Modify: `tests/unit/test_deadline_service.py`

- [ ] **Step 1: Update DeadlineDTO and DeadlineService**

In `regwatch/services/deadlines.py`:

Add `done: bool` to `DeadlineDTO`:

```python
@dataclass
class DeadlineDTO:
    regulation_id: int
    reference_number: str
    title: str
    kind: DeadlineKind
    due_date: date
    days_until: int
    severity_band: str
    url: str
    done: bool
```

Update `upcoming()` to accept `show_completed` and filter done items:

```python
    def upcoming(self, window_days: int, show_completed: bool = False) -> list[DeadlineDTO]:
        rows = (
            self._session.query(Regulation)
            .filter(
                or_(
                    Regulation.transposition_deadline.is_not(None),
                    Regulation.application_date.is_not(None),
                )
            )
            .all()
        )
        today = date.today()
        items: list[DeadlineDTO] = []
        for reg in rows:
            for kind, due, done_flag in (
                ("TRANSPOSITION", reg.transposition_deadline, reg.transposition_done),
                ("APPLICATION", reg.application_date, reg.application_done),
            ):
                if due is None:
                    continue
                if done_flag and not show_completed:
                    continue
                days_until = (due - today).days
                if days_until > window_days:
                    continue
                items.append(
                    DeadlineDTO(
                        regulation_id=reg.regulation_id,
                        reference_number=reg.reference_number,
                        title=reg.title,
                        kind=kind,
                        due_date=due,
                        days_until=days_until,
                        severity_band=self.severity_band(days_until),
                        url=reg.url,
                        done=done_flag,
                    )
                )
        items.sort(key=lambda d: d.days_until)
        return items
```

Add `set_done` method:

```python
    def set_done(self, regulation_id: int, kind: DeadlineKind, done: bool) -> None:
        reg = self._session.get(Regulation, regulation_id)
        if reg is None:
            raise ValueError(f"Regulation {regulation_id} not found")
        if kind == "TRANSPOSITION":
            reg.transposition_done = done
        else:
            reg.application_done = done
```

- [ ] **Step 2: Update deadlines route**

In `regwatch/web/routes/deadlines.py`:

```python
"""Deadlines route."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from regwatch.services.deadlines import DeadlineKind, DeadlineService

router = APIRouter()


@router.get("/deadlines", response_class=HTMLResponse)
def deadlines(
    request: Request,
    show_completed: bool = False,
) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        items = svc.upcoming(window_days=730, show_completed=show_completed)
    return templates.TemplateResponse(
        request,
        "deadlines/list.html",
        {"active": "deadlines", "deadlines": items, "show_completed": show_completed},
    )


@router.post("/deadlines/{regulation_id}/dismiss", response_class=HTMLResponse)
def dismiss_deadline(
    request: Request,
    regulation_id: int,
    kind: DeadlineKind = Form(...),
) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        svc.set_done(regulation_id, kind, done=True)
        session.commit()
    return HTMLResponse("")


@router.post("/deadlines/{regulation_id}/restore", response_class=HTMLResponse)
def restore_deadline(
    request: Request,
    regulation_id: int,
    kind: DeadlineKind = Form(...),
) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = DeadlineService(session)
        svc.set_done(regulation_id, kind, done=False)
        session.commit()
    return HTMLResponse("")
```

- [ ] **Step 3: Update deadlines template**

Replace `regwatch/web/templates/deadlines/list.html`:

```html
{% extends "base.html" %}
{% block title %}RegWatch — Deadlines{% endblock %}
{% block content %}
  <div class="flex justify-between items-center mb-4">
    <h1 class="text-2xl font-bold">Deadlines</h1>
    <label class="flex items-center gap-2 text-sm">
      <input type="checkbox"
             {% if show_completed %}checked{% endif %}
             onchange="window.location.href='/deadlines?show_completed=' + this.checked">
      Show completed
    </label>
  </div>
  <table class="w-full bg-white border rounded shadow-sm text-sm">
    <thead class="bg-slate-100 text-xs uppercase text-slate-600">
      <tr>
        <th class="text-left p-2">Reference</th>
        <th class="text-left p-2">Title</th>
        <th class="text-left p-2">Kind</th>
        <th class="text-left p-2">Due</th>
        <th class="text-left p-2">Days</th>
        <th class="text-left p-2">Band</th>
        <th class="text-left p-2">Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for d in deadlines %}
      <tr id="deadline-{{ d.regulation_id }}-{{ d.kind }}" class="border-t {% if d.done %}opacity-50{% endif %}">
        <td class="p-2 font-mono">{{ d.reference_number }}</td>
        <td class="p-2">{{ d.title }}</td>
        <td class="p-2">{{ d.kind }}</td>
        <td class="p-2">{{ d.due_date }}</td>
        <td class="p-2">{{ d.days_until }}</td>
        <td class="p-2">
          <span class="px-2 py-0.5 rounded text-xs font-semibold
            {% if d.severity_band == 'OVERDUE' %}bg-red-200 text-red-900
            {% elif d.severity_band == 'RED' %}bg-red-100 text-red-800
            {% elif d.severity_band == 'AMBER' %}bg-amber-100 text-amber-800
            {% elif d.severity_band == 'BLUE' %}bg-blue-100 text-blue-800
            {% else %}bg-slate-100 text-slate-700{% endif %}">
            {{ d.severity_band }}
          </span>
        </td>
        <td class="p-2">
          {% if not d.done %}
          <button class="px-2 py-1 bg-green-100 rounded hover:bg-green-200 text-green-800 text-xs"
                  hx-post="/deadlines/{{ d.regulation_id }}/dismiss"
                  hx-vals='{"kind": "{{ d.kind }}"}'
                  hx-target="#deadline-{{ d.regulation_id }}-{{ d.kind }}"
                  hx-swap="outerHTML">Done</button>
          <button class="px-2 py-1 bg-slate-100 rounded hover:bg-slate-200 text-slate-600 text-xs"
                  hx-post="/deadlines/{{ d.regulation_id }}/dismiss"
                  hx-vals='{"kind": "{{ d.kind }}"}'
                  hx-target="#deadline-{{ d.regulation_id }}-{{ d.kind }}"
                  hx-swap="outerHTML">N/A</button>
          {% else %}
          <button class="px-2 py-1 bg-slate-100 rounded hover:bg-slate-200 text-slate-600 text-xs"
                  hx-post="/deadlines/{{ d.regulation_id }}/restore"
                  hx-vals='{"kind": "{{ d.kind }}"}'
                  hx-target="#deadline-{{ d.regulation_id }}-{{ d.kind }}"
                  hx-swap="outerHTML">Restore</button>
          {% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="7" class="p-4 text-center text-slate-500">No upcoming deadlines.</td></tr>
      {% endfor %}
    </tbody>
  </table>
{% endblock %}
```

- [ ] **Step 4: Update deadline service tests**

Update `tests/unit/test_deadline_service.py` to test the `done` field and `set_done` method.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_deadline_service.py tests/integration/test_drafts_deadlines_ict_views.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add regwatch/services/deadlines.py regwatch/web/routes/deadlines.py regwatch/web/templates/deadlines/list.html tests/unit/test_deadline_service.py
git commit -m "feat: add deadline dismissal (done/n/a) with show completed toggle"
```

---

## Task 9: Inbox Enhancements — Model, Service, and Route Changes

**Files:**
- Modify: `regwatch/services/inbox.py`
- Modify: `regwatch/web/routes/inbox.py`
- Modify: `regwatch/web/templates/inbox/list.html`
- Modify: `regwatch/web/templates/partials/inbox_row.html`

- [ ] **Step 1: Add source display names and update InboxService**

In `regwatch/services/inbox.py`:

```python
SOURCE_DISPLAY_NAMES: dict[str, str] = {
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

Update `UpdateEventDTO`:

```python
@dataclass
class UpdateEventDTO:
    event_id: int
    source: str
    source_display_name: str
    source_url: str
    title: str
    published_at: datetime
    severity: str
    review_status: str
    is_ict: bool | None
    seen_at: datetime | None
    description: str | None
    applicable_entity_types: list[str] | None
```

Update `_to_dto`:

```python
def _to_dto(ev: UpdateEvent) -> UpdateEventDTO:
    return UpdateEventDTO(
        event_id=ev.event_id,
        source=ev.source,
        source_display_name=SOURCE_DISPLAY_NAMES.get(ev.source, ev.source),
        source_url=ev.source_url,
        title=ev.title,
        published_at=ev.published_at,
        severity=ev.severity,
        review_status=ev.review_status,
        is_ict=ev.is_ict,
        seen_at=ev.seen_at,
        description=ev.description,
        applicable_entity_types=ev.applicable_entity_types,
    )
```

Add filtered list method to `InboxService`:

```python
    def list_new(
        self,
        *,
        source_display: str | None = None,
        entity_type: str | None = None,
        authorization_types: list[str] | None = None,
        show_all: bool = False,
    ) -> list[UpdateEventDTO]:
        severity_rank = case(
            _SEVERITY_ORDER,
            value=UpdateEvent.severity,
            else_=3,
        )
        query = (
            self._session.query(UpdateEvent)
            .filter(UpdateEvent.review_status == "NEW")
        )

        # Filter by source display name
        if source_display:
            matching_sources = [
                k for k, v in SOURCE_DISPLAY_NAMES.items() if v == source_display
            ]
            if matching_sources:
                query = query.filter(UpdateEvent.source.in_(matching_sources))

        rows = query.order_by(severity_rank, desc(UpdateEvent.published_at)).all()
        dtos = [_to_dto(r) for r in rows]

        # Client-side filtering for entity types (JSON column)
        if not show_all and authorization_types:
            dtos = [
                d for d in dtos
                if d.applicable_entity_types is None  # unclassified — show it
                or "ALL" in d.applicable_entity_types
                or any(t in d.applicable_entity_types for t in authorization_types)
            ]

        if entity_type:
            dtos = [
                d for d in dtos
                if d.applicable_entity_types is not None
                and (entity_type in d.applicable_entity_types or "ALL" in d.applicable_entity_types)
            ]

        return dtos
```

- [ ] **Step 2: Update inbox route**

In `regwatch/web/routes/inbox.py`:

```python
"""Inbox routes: list, detail, and HTMX triage actions."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.inbox import SOURCE_DISPLAY_NAMES, InboxService

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("", response_class=HTMLResponse)
def inbox_list(
    request: Request,
    source: str | None = None,
    entity_type: str | None = None,
    show_all: bool = False,
) -> HTMLResponse:
    templates = request.app.state.templates
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        events = svc.list_new(
            source_display=source,
            entity_type=entity_type,
            authorization_types=auth_types,
            show_all=show_all,
        )
    source_options = sorted(set(SOURCE_DISPLAY_NAMES.values()))
    return templates.TemplateResponse(
        request,
        "inbox/list.html",
        {
            "active": "inbox",
            "events": events,
            "source_options": source_options,
            "current_source": source,
            "current_entity_type": entity_type,
            "show_all": show_all,
        },
    )


@router.post("/{event_id}/mark-seen", response_class=HTMLResponse)
def mark_seen(request: Request, event_id: int) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        svc.mark_seen(event_id)
        session.commit()
    return HTMLResponse("")


@router.post("/{event_id}/archive", response_class=HTMLResponse)
def archive(request: Request, event_id: int) -> HTMLResponse:
    with request.app.state.session_factory() as session:
        svc = InboxService(session)
        svc.archive(event_id)
        session.commit()
    return HTMLResponse("")
```

- [ ] **Step 3: Update inbox list template**

Replace `regwatch/web/templates/inbox/list.html`:

```html
{% extends "base.html" %}
{% block title %}RegWatch — Inbox{% endblock %}
{% block content %}
  <h1 class="text-2xl font-bold mb-4">Inbox ({{ events|length }} new)</h1>

  <div class="flex gap-3 mb-4 items-center text-sm">
    <form class="flex gap-2 items-center">
      <label class="font-medium">Source:</label>
      <select name="source" onchange="this.form.submit()" class="border rounded px-2 py-1 text-sm">
        <option value="">All</option>
        {% for s in source_options %}
        <option value="{{ s }}" {% if current_source == s %}selected{% endif %}>{{ s }}</option>
        {% endfor %}
      </select>
      <label class="font-medium ml-3">Entity Type:</label>
      <select name="entity_type" onchange="this.form.submit()" class="border rounded px-2 py-1 text-sm">
        <option value="">All (relevant)</option>
        <option value="AIFM" {% if current_entity_type == 'AIFM' %}selected{% endif %}>AIFM</option>
        <option value="CHAPTER15_MANCO" {% if current_entity_type == 'CHAPTER15_MANCO' %}selected{% endif %}>Chapter 15 ManCo</option>
      </select>
      <label class="flex items-center gap-1 ml-3">
        <input type="checkbox" name="show_all" value="true"
               {% if show_all %}checked{% endif %}
               onchange="this.form.submit()">
        Show all
      </label>
    </form>
  </div>

  <table class="w-full bg-white border rounded shadow-sm">
    <thead class="bg-slate-100 text-xs uppercase text-slate-600">
      <tr>
        <th class="text-left p-2">Severity</th>
        <th class="text-left p-2">Source</th>
        <th class="text-left p-2">Title</th>
        <th class="text-left p-2">Entity Types</th>
        <th class="text-left p-2">Published</th>
        <th class="text-left p-2">Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for ev in events %}
        {% include "partials/inbox_row.html" %}
      {% else %}
        <tr><td colspan="6" class="p-4 text-center text-slate-500">No new updates.</td></tr>
      {% endfor %}
    </tbody>
  </table>
{% endblock %}
```

- [ ] **Step 4: Update inbox row partial**

Replace `regwatch/web/templates/partials/inbox_row.html`:

```html
<tr id="event-{{ ev.event_id }}" class="border-t text-sm">
  <td class="p-2">
    <span class="px-2 py-0.5 rounded text-xs font-semibold
      {% if ev.severity == 'CRITICAL' %}bg-red-100 text-red-800
      {% elif ev.severity == 'MATERIAL' %}bg-amber-100 text-amber-800
      {% else %}bg-slate-100 text-slate-700{% endif %}">
      {{ ev.severity }}
    </span>
  </td>
  <td class="p-2">{{ ev.source_display_name }}</td>
  <td class="p-2">
    <a class="hover:underline font-medium" href="{{ ev.source_url }}" target="_blank" rel="noreferrer">{{ ev.title }}</a>
    {% if ev.description %}
    <div class="text-xs text-slate-500 mt-0.5">{{ ev.description }}</div>
    {% endif %}
  </td>
  <td class="p-2 text-xs">
    {% if ev.applicable_entity_types %}
      {% for t in ev.applicable_entity_types %}
        <span class="px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded">{{ t }}</span>
      {% endfor %}
    {% else %}
      <span class="text-slate-400">—</span>
    {% endif %}
  </td>
  <td class="p-2">{{ ev.published_at.strftime('%Y-%m-%d') }}</td>
  <td class="p-2 flex gap-1">
    <button class="px-2 py-1 bg-slate-200 rounded hover:bg-slate-300"
            hx-post="/inbox/{{ ev.event_id }}/mark-seen"
            hx-target="#event-{{ ev.event_id }}"
            hx-swap="outerHTML">Mark seen</button>
    <button class="px-2 py-1 bg-red-100 rounded hover:bg-red-200 text-red-800"
            hx-post="/inbox/{{ ev.event_id }}/archive"
            hx-target="#event-{{ ev.event_id }}"
            hx-swap="outerHTML">Archive</button>
  </td>
</tr>
```

- [ ] **Step 5: Run inbox tests**

Run: `pytest tests/unit/test_inbox_service.py tests/integration/test_inbox_view.py -v`
Expected: PASS (update tests if they check DTO fields)

- [ ] **Step 6: Commit**

```bash
git add regwatch/services/inbox.py regwatch/web/routes/inbox.py regwatch/web/templates/inbox/list.html regwatch/web/templates/partials/inbox_row.html
git commit -m "feat: inbox filtering by source/entity type, descriptions, display names"
```

---

## Task 10: Pipeline — Entity Type Classification and Description Generation

**Files:**
- Modify: `regwatch/pipeline/match/classify.py`
- Modify: `regwatch/pipeline/pipeline_factory.py`
- Modify: `regwatch/pipeline/persist.py`
- Modify: `regwatch/domain/types.py` (if `MatchedDocument` needs new fields)

- [ ] **Step 1: Add LLM-based ICT classification to classify.py**

In `regwatch/pipeline/match/classify.py`, add:

```python
"""Keyword heuristics for `is_ict` and severity, with optional LLM fallback."""
from __future__ import annotations

import json
import logging

from regwatch.llm.client import LLMClient, LLMError

logger = logging.getLogger(__name__)

_ICT_KEYWORDS = (
    "dora",
    "ict",
    "cyber",
    "operational resilience",
    "outsourcing",
    "tlpt",
    "third-party provider",
    "third party provider",
    "incident reporting",
    "digital operational resilience",
)


def is_ict_document(text: str, *, llm: LLMClient | None = None) -> bool:
    lower = text.lower()
    if any(kw in lower for kw in _ICT_KEYWORDS):
        return True
    if llm is None:
        return False
    try:
        reply = llm.chat(
            system="You classify regulatory documents. Respond with ONLY \"true\" or \"false\".",
            user=(
                "Is this document related to ICT, cybersecurity, digital operational resilience, "
                "IT outsourcing, or similar technology risk topics?\n\n"
                f"Text (first 2000 chars): {text[:2000]}"
            ),
        )
        return reply.strip().lower() == "true"
    except Exception:  # noqa: BLE001
        logger.warning("LLM ICT classification unavailable, using keyword-only")
        return False


def classify_entity_types(
    title: str, text: str, *, llm: LLMClient | None = None
) -> list[str] | None:
    """Use the LLM to determine which entity types a document applies to."""
    if llm is None:
        return None
    try:
        reply = llm.chat(
            system=(
                "You analyze regulatory documents to determine which types of financial "
                "entities they apply to. Respond with ONLY a JSON array of entity type "
                "strings. Common types: \"AIFM\" (Alternative Investment Fund Manager), "
                "\"CHAPTER15_MANCO\" (UCITS Management Company), \"CASP\" (Crypto-Asset "
                "Service Provider), \"CREDIT_INSTITUTION\", \"INVESTMENT_FIRM\", "
                "\"INSURANCE\", \"PENSION_FUND\". If the document applies broadly to all "
                "financial entities, respond with [\"ALL\"]."
            ),
            user=f"Which entity types does this document apply to?\n\nTitle: {title}\nText (first 2000 chars): {text[:2000]}",
        )
        data = json.loads(reply.strip())
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception:  # noqa: BLE001
        logger.warning("LLM entity type classification unavailable")
    return None


def generate_description(
    title: str, text: str, raw_payload: dict | None, *, llm: LLMClient | None = None
) -> str | None:
    """Extract or generate a short description for an update event."""
    # Try RSS feed description first
    if raw_payload:
        desc = raw_payload.get("description", "")
        if desc and isinstance(desc, str) and len(desc.strip()) > 10:
            return desc.strip()[:500]

    if llm is None:
        return None
    try:
        reply = llm.chat(
            system="Summarize this regulatory document in 1-2 sentences for a compliance officer. Be concise.",
            user=f"{title}\n\n{text[:2000]}",
        )
        return reply.strip()[:500]
    except Exception:  # noqa: BLE001
        logger.warning("LLM description generation unavailable")
    return None


_AMENDMENT_MARKERS = ("amend", "amending", "repeal", "replacing", "supersede")


def severity_for(*, title: str, is_ict: bool, references_in_force: bool) -> str:
    lower = title.lower()
    is_amendment = any(marker in lower for marker in _AMENDMENT_MARKERS)
    if is_amendment and references_in_force:
        return "CRITICAL" if is_ict else "MATERIAL"
    if is_amendment or references_in_force:
        return "MATERIAL"
    return "INFORMATIONAL"
```

- [ ] **Step 2: Update pipeline_factory.py to pass LLM and use new classifiers**

In `regwatch/pipeline/pipeline_factory.py`, update the `_match` function:

```python
from regwatch.pipeline.match.classify import (
    classify_entity_types,
    generate_description,
    is_ict_document,
    severity_for,
)

# In the _match function:
    def _match(extracted: ExtractedDocument) -> MatchedDocument:
        text_for_match = (
            extracted.pdf_extracted_text
            or extracted.html_text
            or extracted.raw.title
            or ""
        )
        references = combined.match(text_for_match)
        is_ict = is_ict_document(
            extracted.raw.title + " " + (text_for_match or ""),
            llm=ollama_client,
        )
        entity_types = classify_entity_types(
            extracted.raw.title, text_for_match, llm=ollama_client
        )
        description = generate_description(
            extracted.raw.title,
            text_for_match,
            extracted.raw.raw_payload,
            llm=ollama_client,
        )
        lifecycle = classify_lifecycle(
            title=extracted.raw.title,
            celex_id=None,
            url=extracted.raw.source_url,
            application_date=None,
            today=date.today(),
        )
        severity = severity_for(
            title=extracted.raw.title,
            is_ict=is_ict,
            references_in_force=bool(references),
        )
        return MatchedDocument(
            extracted=extracted,
            references=references,
            lifecycle_stage=lifecycle,
            is_ict=is_ict,
            severity=severity,
            applicable_entity_types=entity_types,
            description=description,
        )
```

- [ ] **Step 3: Update MatchedDocument in domain/types.py**

Add `applicable_entity_types` and `description` fields:

```python
@dataclass
class MatchedDocument:
    extracted: ExtractedDocument
    references: list[MatchedReference]
    lifecycle_stage: str
    is_ict: bool
    severity: str
    applicable_entity_types: list[str] | None = None
    description: str | None = None
```

- [ ] **Step 4: Update persist.py to save new fields**

In `regwatch/pipeline/persist.py`, update the `UpdateEvent` creation (around line 44):

```python
    event = UpdateEvent(
        source=raw.source,
        source_url=raw.source_url,
        title=raw.title,
        published_at=raw.published_at,
        fetched_at=raw.fetched_at,
        raw_payload=raw.raw_payload,
        content_hash=content_hash,
        is_ict=matched.is_ict,
        severity=matched.severity,
        review_status="NEW",
        description=matched.description,
        applicable_entity_types=matched.applicable_entity_types,
    )
```

- [ ] **Step 5: Run pipeline tests**

Run: `pytest tests/unit/test_classify_heuristics.py tests/integration/test_persist.py tests/integration/test_pipeline_end_to_end.py -v`
Expected: PASS (update tests that check MatchedDocument construction)

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/match/classify.py regwatch/pipeline/pipeline_factory.py regwatch/pipeline/persist.py regwatch/domain/types.py
git commit -m "feat: LLM-based ICT classification, entity-type detection, and description generation in pipeline"
```

---

## Task 11: Discovery Service — LLM-Driven Regulation Classification

**Files:**
- Create: `regwatch/services/discovery.py`
- Create: `tests/unit/test_discovery_service.py`

- [ ] **Step 1: Write tests for the discovery service**

Create `tests/unit/test_discovery_service.py`:

```python
"""Tests for LLM-driven regulation discovery and classification."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from regwatch.db.models import Base, Regulation, RegulationOverride, RegulationType, LifecycleStage
from regwatch.services.discovery import DiscoveryService


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)()


def _add_regulation(session: Session, ref: str, is_ict: bool = False) -> Regulation:
    reg = Regulation(
        reference_number=ref,
        type=RegulationType.CSSF_CIRCULAR,
        title=f"Test regulation {ref}",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=is_ict,
        url=f"https://example.com/{ref}",
        source_of_truth="SEED",
    )
    session.add(reg)
    session.flush()
    return reg


def test_classify_updates_ict_flag(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_regulation(session, "CSSF 18/698")

    llm = MagicMock()
    llm.chat.return_value = '{"is_ict": true, "dora_pillar": "THIRD_PARTY_RISK", "applicable_entity_types": ["ALL"], "is_superseded": false, "superseded_by": null, "confidence": 0.95}'

    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog()
    session.commit()

    session.refresh(reg)
    assert reg.is_ict is True


def test_override_prevents_reclassification(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_regulation(session, "CSSF 18/698", is_ict=True)

    from datetime import UTC, datetime
    session.add(RegulationOverride(
        regulation_id=reg.regulation_id,
        reference_number="CSSF 18/698",
        action="UNSET_ICT",
        created_at=datetime.now(UTC),
    ))
    session.flush()

    llm = MagicMock()
    llm.chat.return_value = '{"is_ict": true, "dora_pillar": "THIRD_PARTY_RISK", "applicable_entity_types": ["ALL"], "is_superseded": false, "superseded_by": null, "confidence": 0.9}'

    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog()
    session.commit()

    session.refresh(reg)
    assert reg.is_ict is False  # Override wins


def test_low_confidence_flags_needs_review(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add_regulation(session, "CSSF 18/698")

    llm = MagicMock()
    llm.chat.return_value = '{"is_ict": true, "dora_pillar": null, "applicable_entity_types": ["ALL"], "is_superseded": false, "superseded_by": null, "confidence": 0.5}'

    svc = DiscoveryService(session, llm=llm)
    svc.classify_catalog()
    session.commit()

    session.refresh(reg)
    assert reg.needs_review is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_discovery_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the discovery service**

Create `regwatch/services/discovery.py`:

```python
"""LLM-driven regulation discovery and classification."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from regwatch.db.models import (
    DoraPillar,
    Regulation,
    RegulationOverride,
)
from regwatch.llm.client import LLMClient

logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = (
    "You are a regulatory classification expert for Luxembourg financial entities.\n"
    "Given a regulation or circular, determine:\n"
    "1. is_ict: Is this related to ICT, cybersecurity, digital operational resilience, "
    "IT outsourcing, or similar technology risk topics? (true/false)\n"
    "2. dora_pillar: If is_ict is true, which DORA pillar? "
    "(ICT_RISK_MGMT, INCIDENT_REPORTING, RESILIENCE_TESTING, THIRD_PARTY_RISK, INFO_SHARING, or null)\n"
    "3. applicable_entity_types: Which entity types does this apply to? "
    '(JSON array of: "AIFM", "CHAPTER15_MANCO", "CREDIT_INSTITUTION", "CASP", '
    '"INVESTMENT_FIRM", "INSURANCE", "PENSION_FUND", or "ALL")\n'
    "4. is_superseded: Has this been replaced by a newer version? (true/false)\n"
    "5. superseded_by: If superseded, the reference number of the replacement (or null)\n"
    "6. confidence: How confident are you in this classification? (0.0 to 1.0)\n\n"
    "Respond with ONLY a JSON object with these 6 fields."
)

_DISCOVER_SYSTEM = (
    "You are a regulatory expert for Luxembourg. Given an entity with authorization "
    "types {auth_types}, list CSSF circulars and EU regulations relevant to "
    "ICT/DORA that may be missing from the following catalog. Only include currently "
    "applicable regulations (not superseded ones). Respond with a JSON array of objects: "
    '{{reference_number, title, issuing_authority, type, is_ict, dora_pillar, url, applicability}}.'
)


class DiscoveryService:
    def __init__(self, session: Session, *, llm: LLMClient) -> None:
        self._session = session
        self._llm = llm

    def classify_catalog(self) -> int:
        """Classify all regulations in the catalog. Returns count of updated regulations."""
        overrides = self._load_overrides()
        regulations = self._session.query(Regulation).all()
        updated = 0

        for reg in regulations:
            ref = reg.reference_number

            # Check for user overrides
            ict_override = overrides.get((ref, "SET_ICT")) or overrides.get((ref, "UNSET_ICT"))
            if ict_override:
                if ict_override.action == "SET_ICT":
                    reg.is_ict = True
                    reg.needs_review = False
                elif ict_override.action == "UNSET_ICT":
                    reg.is_ict = False
                    reg.needs_review = False
                updated += 1
                continue

            # Excluded regulations are skipped
            if (ref, "EXCLUDE") in overrides:
                continue

            # Ask the LLM to classify
            try:
                result = self._classify_regulation(reg)
            except Exception:  # noqa: BLE001
                logger.warning("LLM classification failed for %s", ref)
                continue

            if result is None:
                continue

            reg.is_ict = result.get("is_ict", False)
            pillar = result.get("dora_pillar")
            if pillar and reg.is_ict:
                try:
                    reg.dora_pillar = DoraPillar(pillar)
                except ValueError:
                    reg.dora_pillar = None
            else:
                reg.dora_pillar = None

            confidence = result.get("confidence", 1.0)
            reg.needs_review = confidence < 0.7

            updated += 1

        self._session.flush()
        return updated

    def discover_missing(self, auth_types: list[str]) -> int:
        """Ask the LLM to suggest missing regulations. Returns count of new regulations."""
        existing = self._session.query(Regulation).all()
        overrides = self._load_overrides()

        catalog_text = "\n".join(
            f"- {r.reference_number}: {r.title}" for r in existing
        )

        try:
            reply = self._llm.chat(
                system=_DISCOVER_SYSTEM.format(auth_types=", ".join(auth_types)),
                user=f"Current catalog:\n{catalog_text}",
            )
            data = json.loads(reply.strip())
            if not isinstance(data, list):
                return 0
        except Exception:  # noqa: BLE001
            logger.warning("LLM regulation discovery failed")
            return 0

        added = 0
        existing_refs = {r.reference_number for r in existing}
        for item in data:
            ref = item.get("reference_number", "")
            if not ref or ref in existing_refs:
                continue
            # Skip if user has explicitly excluded this
            if (ref, "EXCLUDE") in overrides:
                continue

            from regwatch.db.models import RegulationType  # noqa: PLC0415

            try:
                reg_type = RegulationType(item.get("type", "CSSF_CIRCULAR"))
            except ValueError:
                reg_type = RegulationType.CSSF_CIRCULAR

            from regwatch.db.models import LifecycleStage  # noqa: PLC0415

            reg = Regulation(
                reference_number=ref,
                type=reg_type,
                title=item.get("title", ref),
                issuing_authority=item.get("issuing_authority", "Unknown"),
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=item.get("is_ict", False),
                url=item.get("url", ""),
                source_of_truth="DISCOVERED",
                needs_review=True,
            )
            pillar = item.get("dora_pillar")
            if pillar and reg.is_ict:
                try:
                    reg.dora_pillar = DoraPillar(pillar)
                except ValueError:
                    pass
            self._session.add(reg)
            added += 1

        self._session.flush()
        return added

    def _classify_regulation(self, reg: Regulation) -> dict | None:
        reply = self._llm.chat(
            system=_CLASSIFY_SYSTEM,
            user=(
                f"Classify this regulation:\n"
                f"Reference: {reg.reference_number}\n"
                f"Title: {reg.title}\n"
                f"Issuing authority: {reg.issuing_authority}\n"
                f"Type: {reg.type.value}"
            ),
        )
        try:
            return json.loads(reply.strip())
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON for %s", reg.reference_number)
            return None

    def _load_overrides(self) -> dict[tuple[str, str], RegulationOverride]:
        rows = self._session.query(RegulationOverride).all()
        return {(r.reference_number, r.action): r for r in rows}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_discovery_service.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add regwatch/services/discovery.py tests/unit/test_discovery_service.py
git commit -m "feat: add LLM-driven regulation discovery and classification service"
```

---

## Task 12: ICT & Catalog UI — Management Actions

**Files:**
- Modify: `regwatch/web/routes/ict.py`
- Modify: `regwatch/web/routes/catalog.py`
- Modify: `regwatch/web/templates/ict/list.html`
- Modify: `regwatch/services/regulations.py`

- [ ] **Step 1: Add needs_review and dora_pillar to RegulationDTO**

In `regwatch/services/regulations.py`, update `RegulationDTO`:

```python
@dataclass
class RegulationDTO:
    regulation_id: int
    reference_number: str
    title: str
    type: str
    issuing_authority: str
    lifecycle_stage: str
    is_ict: bool
    url: str
    transposition_deadline: date | None
    application_date: date | None
    needs_review: bool
    dora_pillar: str | None
```

Update `_to_dto`:

```python
def _to_dto(r: Regulation) -> RegulationDTO:
    return RegulationDTO(
        regulation_id=r.regulation_id,
        reference_number=r.reference_number,
        title=r.title,
        type=r.type.value,
        issuing_authority=r.issuing_authority,
        lifecycle_stage=r.lifecycle_stage.value,
        is_ict=r.is_ict,
        url=r.url,
        transposition_deadline=r.transposition_deadline,
        application_date=r.application_date,
        needs_review=r.needs_review,
        dora_pillar=r.dora_pillar.value if r.dora_pillar else None,
    )
```

- [ ] **Step 2: Add management routes to ICT and catalog**

In `regwatch/web/routes/ict.py`:

```python
"""ICT / DORA route with management actions."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.db.models import Regulation, RegulationOverride
from regwatch.services.discovery import DiscoveryService
from regwatch.services.regulations import RegulationFilter, RegulationService

router = APIRouter()


@router.get("/ict", response_class=HTMLResponse)
def ict(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        svc = RegulationService(session)
        regs = svc.list(RegulationFilter(is_ict=True))
    return templates.TemplateResponse(
        request,
        "ict/list.html",
        {"active": "ict", "regulations": regs},
    )


@router.post("/ict/{regulation_id}/unset-ict")
def unset_ict(request: Request, regulation_id: int) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        reg = session.get(Regulation, regulation_id)
        if reg:
            reg.is_ict = False
            reg.needs_review = False
            session.add(RegulationOverride(
                regulation_id=regulation_id,
                reference_number=reg.reference_number,
                action="UNSET_ICT",
                created_at=datetime.now(UTC),
            ))
            session.commit()
    return RedirectResponse(url="/ict", status_code=303)


@router.post("/ict/refresh")
def refresh_ict(request: Request) -> RedirectResponse:
    llm = request.app.state.llm_client
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        svc = DiscoveryService(session, llm=llm)
        svc.classify_catalog()
        svc.discover_missing(auth_types)
        session.commit()
    return RedirectResponse(url="/ict", status_code=303)
```

In `regwatch/web/routes/catalog.py`, add similar management routes:

```python
@router.post("/catalog/{regulation_id}/set-ict")
def set_ict(request: Request, regulation_id: int) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        reg = session.get(Regulation, regulation_id)
        if reg:
            reg.is_ict = True
            reg.needs_review = False
            session.add(RegulationOverride(
                regulation_id=regulation_id,
                reference_number=reg.reference_number,
                action="SET_ICT",
                created_at=datetime.now(UTC),
            ))
            session.commit()
    return RedirectResponse(url="/catalog", status_code=303)


@router.post("/catalog/{regulation_id}/exclude")
def exclude_regulation(request: Request, regulation_id: int) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        reg = session.get(Regulation, regulation_id)
        if reg:
            session.add(RegulationOverride(
                regulation_id=regulation_id,
                reference_number=reg.reference_number,
                action="EXCLUDE",
                created_at=datetime.now(UTC),
            ))
            session.delete(reg)
            session.commit()
    return RedirectResponse(url="/catalog", status_code=303)


@router.post("/catalog/add")
def add_regulation(
    request: Request,
    reference_number: str = Form(...),
    title: str = Form(...),
    reg_type: str = Form("CSSF_CIRCULAR"),
    issuing_authority: str = Form("CSSF"),
    url: str = Form(""),
) -> RedirectResponse:
    from regwatch.db.models import LifecycleStage, RegulationType  # noqa: PLC0415

    with request.app.state.session_factory() as session:
        reg = Regulation(
            reference_number=reference_number,
            type=RegulationType(reg_type),
            title=title,
            issuing_authority=issuing_authority,
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            url=url or f"https://www.cssf.lu/en/Document/circular-{reference_number.lower().replace(' ', '-')}/",
            source_of_truth="MANUAL",
            needs_review=True,
        )
        session.add(reg)
        session.flush()
        session.add(RegulationOverride(
            regulation_id=reg.regulation_id,
            reference_number=reference_number,
            action="INCLUDE",
            created_at=datetime.now(UTC),
        ))
        session.commit()
    return RedirectResponse(url="/catalog", status_code=303)


@router.post("/catalog/refresh")
def refresh_catalog(request: Request) -> RedirectResponse:
    llm = request.app.state.llm_client
    config = request.app.state.config
    auth_types = [a.type for a in config.entity.authorizations]
    with request.app.state.session_factory() as session:
        svc = DiscoveryService(session, llm=llm)
        svc.classify_catalog()
        svc.discover_missing(auth_types)
        session.commit()
    return RedirectResponse(url="/catalog", status_code=303)
```

- [ ] **Step 3: Update ICT template with management buttons**

Replace `regwatch/web/templates/ict/list.html`:

```html
{% extends "base.html" %}
{% block title %}RegWatch — ICT / DORA{% endblock %}
{% block content %}
  <div class="flex justify-between items-center mb-4">
    <h1 class="text-2xl font-bold">ICT / DORA</h1>
    <form method="post" action="/ict/refresh">
      <button type="submit" class="px-3 py-2 bg-slate-800 text-white rounded text-sm hover:bg-slate-700">
        Refresh catalog
      </button>
    </form>
  </div>
  <table class="w-full bg-white border rounded shadow-sm text-sm">
    <thead class="bg-slate-100 text-xs uppercase text-slate-600">
      <tr>
        <th class="text-left p-2">Reference</th>
        <th class="text-left p-2">Title</th>
        <th class="text-left p-2">Authority</th>
        <th class="text-left p-2">DORA Pillar</th>
        <th class="text-left p-2">Lifecycle</th>
        <th class="text-left p-2">Status</th>
        <th class="text-left p-2">Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for r in regulations %}
      <tr class="border-t {% if r.needs_review %}bg-amber-50{% endif %}">
        <td class="p-2 font-mono">{{ r.reference_number }}</td>
        <td class="p-2">
          <a href="{{ r.url }}" target="_blank" class="hover:underline">{{ r.title }}</a>
        </td>
        <td class="p-2">{{ r.issuing_authority }}</td>
        <td class="p-2">{{ r.dora_pillar or '—' }}</td>
        <td class="p-2">{{ r.lifecycle_stage }}</td>
        <td class="p-2">
          {% if r.needs_review %}
            <span class="px-2 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-800">Needs review</span>
          {% else %}
            <span class="text-green-700 text-xs">Confirmed</span>
          {% endif %}
        </td>
        <td class="p-2">
          <form method="post" action="/ict/{{ r.regulation_id }}/unset-ict" class="inline">
            <button type="submit" class="px-2 py-1 bg-red-100 rounded hover:bg-red-200 text-red-800 text-xs">
              Not ICT
            </button>
          </form>
        </td>
      </tr>
      {% else %}
        <tr><td colspan="7" class="p-4 text-center text-slate-500">No ICT-flagged regulations.</td></tr>
      {% endfor %}
    </tbody>
  </table>
{% endblock %}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_regulation_service.py tests/integration/test_drafts_deadlines_ict_views.py -v`
Expected: PASS (update tests for new DTO fields)

- [ ] **Step 5: Commit**

```bash
git add regwatch/web/routes/ict.py regwatch/web/routes/catalog.py regwatch/web/templates/ict/list.html regwatch/services/regulations.py
git commit -m "feat: add ICT/catalog management UI with override tracking and refresh"
```

---

## Task 13: Seed Data — Remove Hardcoded ICT Values

**Files:**
- Modify: `seeds/regulations_seed.yaml`
- Modify: `regwatch/db/seed.py`

- [ ] **Step 1: Set all is_ict to false in the seed**

In `seeds/regulations_seed.yaml`, set `is_ict: false` for ALL regulations (including DORA — the LLM will re-classify on discovery):

All `is_ict:` values should be `false`. The LLM discovery service will set correct values at runtime.

- [ ] **Step 2: Update seed loader to not overwrite LLM-set is_ict**

In `regwatch/db/seed.py`, in `_upsert_regulation`, when the regulation already exists and `is_ict` was set by discovery (not seed), don't overwrite:

```python
    if reg is None:
        reg = Regulation(
            reference_number=reference,
            source_of_truth="SEED",
            type=RegulationType(reg_data["type"]),
            title=reg_data["title"],
            issuing_authority=reg_data["issuing_authority"],
            lifecycle_stage=LifecycleStage(reg_data["lifecycle_stage"]),
            is_ict=reg_data.get("is_ict", False),
            url=reg_data["url"],
        )
        session.add(reg)
    else:
        reg.type = RegulationType(reg_data["type"])
        reg.title = reg_data["title"]
        reg.issuing_authority = reg_data["issuing_authority"]
        reg.lifecycle_stage = LifecycleStage(reg_data["lifecycle_stage"])
        # Don't overwrite is_ict if it was already set by discovery or override
        if reg.source_of_truth == "SEED":
            reg.is_ict = reg_data.get("is_ict", False)
        reg.url = reg_data["url"]
```

- [ ] **Step 3: Run seed tests**

Run: `pytest tests/unit/test_seed_loader.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add seeds/regulations_seed.yaml regwatch/db/seed.py
git commit -m "refactor: remove hardcoded is_ict from seed, preserve LLM-set values"
```

---

## Task 14: Update Existing Tests

**Files:**
- Modify: multiple test files

- [ ] **Step 1: Delete old Ollama test file**

Delete `tests/unit/test_ollama_client.py` (replaced by `tests/unit/test_llm_client.py`).

- [ ] **Step 2: Update test_ollama_refs.py**

Rename imports from `regwatch.ollama.client` to `regwatch.llm.client`, `OllamaClient` to `LLMClient`.

- [ ] **Step 3: Update test_combined_matcher.py**

Rename imports from `regwatch.ollama.client` to `regwatch.llm.client`, `OllamaClient` to `LLMClient`, `OllamaError` to `LLMError`.

- [ ] **Step 4: Update integration test helpers**

In `tests/integration/test_app_smoke.py::_build_config`, the YAML now uses `llm:` instead of `ollama:`. Update the config builder.

Any integration test that sets `client.app.state.ollama_client = MagicMock()` must change to `client.app.state.llm_client = MagicMock()`.

- [ ] **Step 5: Update test_run_pipeline_action.py**

Change `ollama_client` references to `llm_client`.

- [ ] **Step 6: Run full test suite**

Run: `pytest -x`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "test: update all tests for LLMClient rename and new features"
```

---

## Task 15: Integration Test — End-to-End Verification

**Files:**
- Existing test infrastructure

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all tests PASS

- [ ] **Step 2: Run linting and type checking**

Run: `ruff check regwatch`
Run: `mypy regwatch`
Expected: no new errors

- [ ] **Step 3: Start the dev server and verify in browser**

Run: `uvicorn regwatch.main:app --port 8001 --reload`

Manual checks:
1. Settings page shows LLM Server section with model dropdowns
2. If no model configured, redirects to setup page
3. Inbox shows filter dropdowns (source, entity type)
4. Deadlines page shows Done/N/A buttons
5. ICT page shows management buttons and "Refresh catalog"
6. Port is 8001

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "fix: address integration issues found during manual testing"
```

---

## Summary of dependency order

Tasks 1-3 are foundational (LLM client + rename). Task 4 adds DB models needed by everything else. Task 5-6 add settings/startup. Tasks 7-13 are feature-specific and can proceed in any order after 1-6. Task 14 fixes tests broken by the rename. Task 15 is final verification.

```
Task 1 (LLM client) → Task 2 (config rename) → Task 3 (codebase rename) → Task 4 (DB models)
    → Task 5 (settings service) → Task 6 (app startup)
    → Task 7 (settings UI)
    → Task 8 (deadlines)
    → Task 9 (inbox)
    → Task 10 (pipeline classify)
    → Task 11 (discovery service)
    → Task 12 (ICT/catalog UI)
    → Task 13 (seed data)
    → Task 14 (update tests)
    → Task 15 (integration verification)
```
