import httpx
from pytest_httpx import HTTPXMock

from regwatch.ollama.client import OllamaClient


def test_chat_returns_content(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"role": "assistant", "content": "Hello!"}, "done": True},
    )
    client = OllamaClient(
        base_url="http://localhost:11434",
        chat_model="llama3.1:8b",
        embedding_model="nomic-embed-text",
    )
    reply = client.chat(system="sys", user="hi")
    assert reply == "Hello!"


def test_embed_returns_vector(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/embed",
        json={"embeddings": [[0.1, 0.2, 0.3]]},
    )
    client = OllamaClient(
        base_url="http://localhost:11434",
        chat_model="x",
        embedding_model="nomic-embed-text",
    )
    vector = client.embed("some text")
    assert vector == [0.1, 0.2, 0.3]


def test_health_check(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text"}]},
    )
    client = OllamaClient(
        base_url="http://localhost:11434",
        chat_model="llama3.1:8b",
        embedding_model="nomic-embed-text",
    )
    status = client.health()
    assert status.reachable is True
    assert status.chat_model_available is True
    assert status.embedding_model_available is True


def test_health_unreachable(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    client = OllamaClient(
        base_url="http://localhost:11434",
        chat_model="x",
        embedding_model="y",
    )
    status = client.health()
    assert status.reachable is False
