"""Generic LLM client that auto-detects OpenAI-compatible or Ollama API."""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


@dataclass
class HealthStatus:
    reachable: bool
    chat_model_available: bool = False
    embedding_model_available: bool = False


class _ApiFormat(Enum):
    OPENAI = auto()
    OLLAMA = auto()


class LLMClient:
    """HTTP client for an LLM server that speaks either OpenAI or Ollama API.

    Auto-detection runs on the first call to :meth:`health`.  Before that, all
    requests are routed using the OpenAI format (the more widely supported one).
    """

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
        self._format: _ApiFormat = _ApiFormat.OPENAI  # default until health() detects

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, *, system: str, user: str) -> str:
        """Return the assistant's reply to *user* given *system* instructions."""
        if self._format is _ApiFormat.OLLAMA:
            return self._ollama_chat(system=system, user=user)
        return self._openai_chat(system=system, user=user)

    def chat_stream(self, *, system: str, user: str) -> Iterator[str]:
        """Yield content chunks from a streaming chat response."""
        if self._format is _ApiFormat.OLLAMA:
            yield from self._ollama_chat_stream(system=system, user=user)
        else:
            yield from self._openai_chat_stream(system=system, user=user)

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for *text*."""
        if self._format is _ApiFormat.OLLAMA:
            return self._ollama_embed(text)
        return self._openai_embed(text)

    def health(self) -> HealthStatus:
        """Probe the server, auto-detect API format, and return availability."""
        # Try OpenAI /v1/models first
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self._base_url}/v1/models")
                response.raise_for_status()
                data = response.json()
            if "data" in data:
                self._format = _ApiFormat.OPENAI
                names = {m.get("id", "") for m in data["data"]}
                return HealthStatus(
                    reachable=True,
                    chat_model_available=self._model_available(self._chat_model, names),
                    embedding_model_available=self._model_available(
                        self._embedding_model, names
                    ),
                )
        except Exception:  # noqa: BLE001
            pass

        # Fallback: try Ollama /api/tags
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
            if "models" in data:
                self._format = _ApiFormat.OLLAMA
                names = {m.get("name", "") for m in data["models"]}
                return HealthStatus(
                    reachable=True,
                    chat_model_available=self._model_available(self._chat_model, names),
                    embedding_model_available=self._model_available(
                        self._embedding_model, names
                    ),
                )
        except Exception:  # noqa: BLE001
            pass

        return HealthStatus(reachable=False)

    def list_models(self) -> list[str]:
        """Return model identifiers available on the server."""
        if self._format is _ApiFormat.OLLAMA:
            return self._ollama_list_models()
        return self._openai_list_models()

    # ------------------------------------------------------------------
    # OpenAI implementation
    # ------------------------------------------------------------------

    def _openai_chat(self, *, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(f"{self._base_url}/v1/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        try:
            content: str = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError("Unexpected response structure from OpenAI chat endpoint") from exc
        if not content:
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", "?")
            total_tokens = usage.get("total_tokens", "?")
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            if reasoning:
                raise LLMError(
                    f"Empty response: model used {reasoning} reasoning tokens, "
                    f"leaving no room for output "
                    f"(prompt={prompt_tokens}, total={total_tokens}). "
                    f"Increase the model context length in LM Studio."
                )
            raise LLMError(
                f"Empty response from LLM (prompt={prompt_tokens}, total={total_tokens})"
            )
        return content

    def _openai_chat_stream(self, *, system: str, user: str) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("POST", f"{self._base_url}/v1/chat/completions", json=payload) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        line = line[len("data: "):]
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk = (
                        data.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                    )
                    if chunk:
                        yield chunk

    def _openai_embed(self, text: str) -> list[float]:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/v1/embeddings",
                json={"model": self._embedding_model, "input": text},
            )
            response.raise_for_status()
            data = response.json()
        try:
            vector: list[float] = data["data"][0]["embedding"]
        except (KeyError, IndexError) as exc:
            raise LLMError("Unexpected response structure from OpenAI embeddings endpoint") from exc
        if not vector:
            raise LLMError("Empty embeddings response from OpenAI endpoint")
        return vector

    def _openai_list_models(self) -> list[str]:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(f"{self._base_url}/v1/models")
            response.raise_for_status()
            data = response.json()
        return [m.get("id", "") for m in data.get("data", [])]

    # ------------------------------------------------------------------
    # Ollama implementation
    # ------------------------------------------------------------------

    def _ollama_chat(self, *, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError("Empty response from Ollama chat endpoint")
        return content

    def _ollama_chat_stream(self, *, system: str, user: str) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": self._chat_model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("POST", f"{self._base_url}/api/chat", json=payload) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk = data.get("message", {}).get("content")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        return

    def _ollama_embed(self, text: str) -> list[float]:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._embedding_model, "input": text},
            )
            response.raise_for_status()
            data = response.json()
        vectors = data.get("embeddings", [])
        if not vectors:
            raise LLMError("Empty embeddings response from Ollama endpoint")
        return list(vectors[0])

    def _ollama_list_models(self) -> list[str]:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
        return [m.get("name", "") for m in data.get("models", [])]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _model_available(model: str, names: set[str]) -> bool:
        """Return True if *model* appears in *names* (exact or prefix match)."""
        return model in names or any(n.startswith(model.split(":")[0]) for n in names)
