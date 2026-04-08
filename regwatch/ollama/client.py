"""Thin HTTP client for a local Ollama instance."""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx


class OllamaError(RuntimeError):
    pass


@dataclass
class HealthStatus:
    reachable: bool
    chat_model_available: bool = False
    embedding_model_available: bool = False


class OllamaClient:
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

    def chat(self, *, system: str, user: str) -> str:
        payload = {
            "model": self._chat_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return self._post_for_chat(payload)

    def _post_for_chat(self, payload: dict[str, Any]) -> str:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        message = data.get("message", {})
        content = message.get("content", "")
        if not content:
            raise OllamaError("Empty response from Ollama chat endpoint")
        return content

    def chat_stream(self, *, system: str, user: str) -> Iterator[str]:
        """Yield content chunks from a streaming chat response."""
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
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._embedding_model, "input": text},
            )
            response.raise_for_status()
            data = response.json()
        vectors = data.get("embeddings", [])
        if not vectors:
            raise OllamaError("Empty embeddings response")
        return list(vectors[0])

    def health(self) -> HealthStatus:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
        except Exception:  # noqa: BLE001
            return HealthStatus(reachable=False)
        names = {m.get("name", "") for m in data.get("models", [])}
        return HealthStatus(
            reachable=True,
            chat_model_available=self._chat_model in names
            or any(n.startswith(self._chat_model.split(":")[0]) for n in names),
            embedding_model_available=self._embedding_model in names
            or any(
                n.startswith(self._embedding_model.split(":")[0]) for n in names
            ),
        )
