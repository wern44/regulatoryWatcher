"""Tests for the generic LLMClient (OpenAI + Ollama auto-detection)."""
from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from regwatch.llm.client import HealthStatus, LLMClient, LLMError

BASE = "http://localhost:11434"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _openai_client() -> LLMClient:
    """Return a client pre-configured for OpenAI format (no health() call needed)."""
    # Default format is already OPENAI, so just return a fresh instance.
    return LLMClient(base_url=BASE, chat_model="gpt-4o-mini", embedding_model="text-embedding-3-small")


def _ollama_client(httpx_mock: HTTPXMock) -> LLMClient:
    """Return a client that has had health() called and detected Ollama format."""
    httpx_mock.add_response(
        url=f"{BASE}/v1/models",
        status_code=404,
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/tags",
        json={
            "models": [
                {"name": "llama3.1:8b"},
                {"name": "nomic-embed-text"},
            ]
        },
    )
    client = LLMClient(
        base_url=BASE,
        chat_model="llama3.1:8b",
        embedding_model="nomic-embed-text",
    )
    client.health()
    return client


# ---------------------------------------------------------------------------
# Chat — OpenAI format
# ---------------------------------------------------------------------------

def test_chat_openai_format(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/v1/chat/completions",
        json={
            "choices": [
                {"message": {"role": "assistant", "content": "Hello from OpenAI!"}}
            ]
        },
    )
    client = _openai_client()
    reply = client.chat(system="You are helpful.", user="Hi")
    assert reply == "Hello from OpenAI!"


def test_chat_openai_empty_content_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/v1/chat/completions",
        json={"choices": [{"message": {"role": "assistant", "content": ""}}]},
    )
    client = _openai_client()
    with pytest.raises(LLMError):
        client.chat(system="sys", user="user")


# ---------------------------------------------------------------------------
# Chat — Ollama format
# ---------------------------------------------------------------------------

def test_chat_ollama_format(httpx_mock: HTTPXMock) -> None:
    client = _ollama_client(httpx_mock)
    httpx_mock.add_response(
        url=f"{BASE}/api/chat",
        json={"message": {"role": "assistant", "content": "Hello from Ollama!"}, "done": True},
    )
    reply = client.chat(system="You are helpful.", user="Hi")
    assert reply == "Hello from Ollama!"


def test_chat_ollama_empty_content_raises(httpx_mock: HTTPXMock) -> None:
    client = _ollama_client(httpx_mock)
    httpx_mock.add_response(
        url=f"{BASE}/api/chat",
        json={"message": {"role": "assistant", "content": ""}, "done": True},
    )
    with pytest.raises(LLMError):
        client.chat(system="sys", user="user")


# ---------------------------------------------------------------------------
# Embed — OpenAI format
# ---------------------------------------------------------------------------

def test_embed_openai_format(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/v1/embeddings",
        json={"data": [{"embedding": [0.1, 0.2, 0.3]}]},
    )
    client = _openai_client()
    vector = client.embed("some text")
    assert vector == [0.1, 0.2, 0.3]


def test_embed_openai_empty_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/v1/embeddings",
        json={"data": [{"embedding": []}]},
    )
    client = _openai_client()
    with pytest.raises(LLMError):
        client.embed("text")


# ---------------------------------------------------------------------------
# Embed — Ollama format
# ---------------------------------------------------------------------------

def test_embed_ollama_format(httpx_mock: HTTPXMock) -> None:
    client = _ollama_client(httpx_mock)
    httpx_mock.add_response(
        url=f"{BASE}/api/embed",
        json={"embeddings": [[0.4, 0.5, 0.6]]},
    )
    vector = client.embed("some text")
    assert vector == [0.4, 0.5, 0.6]


def test_embed_ollama_empty_raises(httpx_mock: HTTPXMock) -> None:
    client = _ollama_client(httpx_mock)
    httpx_mock.add_response(
        url=f"{BASE}/api/embed",
        json={"embeddings": []},
    )
    with pytest.raises(LLMError):
        client.embed("text")


# ---------------------------------------------------------------------------
# Health — detects OpenAI format via /v1/models
# ---------------------------------------------------------------------------

def test_health_detects_openai(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/v1/models",
        json={
            "data": [
                {"id": "gpt-4o-mini"},
                {"id": "text-embedding-3-small"},
            ]
        },
    )
    client = LLMClient(
        base_url=BASE,
        chat_model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
    )
    status = client.health()
    assert status == HealthStatus(
        reachable=True,
        chat_model_available=True,
        embedding_model_available=True,
    )


def test_health_openai_models_not_found(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/v1/models",
        json={"data": [{"id": "other-model"}]},
    )
    client = LLMClient(
        base_url=BASE,
        chat_model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
    )
    status = client.health()
    assert status.reachable is True
    assert status.chat_model_available is False
    assert status.embedding_model_available is False


# ---------------------------------------------------------------------------
# Health — detects Ollama format via /api/tags fallback
# ---------------------------------------------------------------------------

def test_health_detects_ollama(httpx_mock: HTTPXMock) -> None:
    # /v1/models fails → fall through to /api/tags
    httpx_mock.add_response(url=f"{BASE}/v1/models", status_code=404)
    httpx_mock.add_response(
        url=f"{BASE}/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text"}]},
    )
    client = LLMClient(
        base_url=BASE,
        chat_model="llama3.1:8b",
        embedding_model="nomic-embed-text",
    )
    status = client.health()
    assert status == HealthStatus(
        reachable=True,
        chat_model_available=True,
        embedding_model_available=True,
    )


def test_health_ollama_prefix_match(httpx_mock: HTTPXMock) -> None:
    """Prefix match: 'llama3.1' matches 'llama3.1:8b'."""
    httpx_mock.add_response(url=f"{BASE}/v1/models", status_code=404)
    httpx_mock.add_response(
        url=f"{BASE}/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text:latest"}]},
    )
    client = LLMClient(
        base_url=BASE,
        chat_model="llama3.1",
        embedding_model="nomic-embed-text",
    )
    status = client.health()
    assert status.reachable is True
    assert status.chat_model_available is True
    assert status.embedding_model_available is True


# ---------------------------------------------------------------------------
# Health — unreachable server
# ---------------------------------------------------------------------------

def test_health_unreachable(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=f"{BASE}/v1/models")
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=f"{BASE}/api/tags")
    client = LLMClient(base_url=BASE, chat_model="x", embedding_model="y")
    status = client.health()
    assert status == HealthStatus(reachable=False)


# ---------------------------------------------------------------------------
# list_models — OpenAI format
# ---------------------------------------------------------------------------

def test_list_models_openai(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/v1/models",
        json={"data": [{"id": "gpt-4o-mini"}, {"id": "text-embedding-3-small"}]},
    )
    client = _openai_client()
    models = client.list_models()
    assert "gpt-4o-mini" in models
    assert "text-embedding-3-small" in models


# ---------------------------------------------------------------------------
# list_models — Ollama format
# ---------------------------------------------------------------------------

def test_list_models_ollama(httpx_mock: HTTPXMock) -> None:
    client = _ollama_client(httpx_mock)
    httpx_mock.add_response(
        url=f"{BASE}/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text"}]},
    )
    models = client.list_models()
    assert "llama3.1:8b" in models
    assert "nomic-embed-text" in models


# ---------------------------------------------------------------------------
# Default to OpenAI when health() has NOT been called
# ---------------------------------------------------------------------------

def test_default_openai_format_without_health(httpx_mock: HTTPXMock) -> None:
    """Without calling health(), client should default to OpenAI routing."""
    httpx_mock.add_response(
        url=f"{BASE}/v1/chat/completions",
        json={"choices": [{"message": {"role": "assistant", "content": "default openai"}}]},
    )
    client = LLMClient(
        base_url=BASE,
        chat_model="some-model",
        embedding_model="some-embed",
    )
    # Must NOT call health() — this verifies the default format
    reply = client.chat(system="sys", user="hi")
    assert reply == "default openai"


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

def test_chat_model_property() -> None:
    client = LLMClient(base_url=BASE, chat_model="model-a", embedding_model="emb-a")
    assert client.chat_model == "model-a"
    client.chat_model = "model-b"
    assert client.chat_model == "model-b"


def test_embedding_model_property() -> None:
    client = LLMClient(base_url=BASE, chat_model="model-a", embedding_model="emb-a")
    assert client.embedding_model == "emb-a"
    client.embedding_model = "emb-b"
    assert client.embedding_model == "emb-b"


# ---------------------------------------------------------------------------
# Streaming — OpenAI SSE format
# ---------------------------------------------------------------------------

def test_chat_stream_openai(httpx_mock: HTTPXMock) -> None:
    sse_lines = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n'
        'data: {"choices":[{"delta":{"content":" world"}}]}\n'
        "data: [DONE]\n"
    )
    httpx_mock.add_response(
        url=f"{BASE}/v1/chat/completions",
        text=sse_lines,
    )
    client = _openai_client()
    chunks = list(client.chat_stream(system="sys", user="hi"))
    assert chunks == ["Hello", " world"]


# ---------------------------------------------------------------------------
# Streaming — Ollama NDJSON format
# ---------------------------------------------------------------------------

def test_chat_stream_ollama(httpx_mock: HTTPXMock) -> None:
    ndjson = (
        json.dumps({"message": {"content": "Hi"}, "done": False}) + "\n"
        + json.dumps({"message": {"content": " there"}, "done": False}) + "\n"
        + json.dumps({"message": {"content": ""}, "done": True}) + "\n"
    )
    client = _ollama_client(httpx_mock)
    httpx_mock.add_response(
        url=f"{BASE}/api/chat",
        text=ndjson,
    )
    chunks = list(client.chat_stream(system="sys", user="hi"))
    assert chunks == ["Hi", " there"]
