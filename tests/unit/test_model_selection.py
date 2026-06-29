"""Unit tests for chat-model auto-selection."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import Base, Setting
from regwatch.llm.client import HealthStatus
from regwatch.llm.model_selection import (
    choose_chat_model,
    estimate_param_billions,
    is_available,
    refresh_chat_model,
)
from regwatch.services.settings import SettingsService


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("qwen2.5-7b-instruct", 7.0),
        ("llama-3.1-70b", 70.0),
        ("phi-3-mini-0.5b", 0.5),
        ("Mistral-7B-Instruct", 7.0),
        ("nomic-embed-text", 0.0),
        ("", 0.0),
        ("qwen3-1.5b-and-30b-moe", 30.0),
    ],
)
def test_estimate_param_billions(name: str, expected: float) -> None:
    assert estimate_param_billions(name) == expected


def test_is_available_exact_and_prefix() -> None:
    available = ["llama3:latest", "qwen2.5-7b-instruct"]
    assert is_available("qwen2.5-7b-instruct", available)
    assert is_available("llama3", available)  # prefix before ":tag"
    assert not is_available("missing-model", available)
    assert not is_available("", available)


def test_choose_keeps_current_when_available() -> None:
    available = ["a-7b", "b-14b"]
    assert choose_chat_model(available, "a-7b") == "a-7b"


def test_choose_only_model_when_single() -> None:
    assert choose_chat_model(["solo-3b"], None) == "solo-3b"
    assert choose_chat_model(["solo-3b"], "") == "solo-3b"


def test_choose_largest_when_multiple_and_unset() -> None:
    available = ["small-7b", "big-32b", "mid-14b"]
    assert choose_chat_model(available, None) == "big-32b"


def test_choose_repairs_when_current_gone() -> None:
    available = ["small-7b", "big-32b"]
    assert choose_chat_model(available, "removed-13b") == "big-32b"


def test_choose_prefers_non_embedding_models() -> None:
    # The embedding model has a larger nominal size but should not be picked
    # as the chat model while a real chat model exists.
    available = ["nomic-embed-text-137b", "chat-7b"]
    assert choose_chat_model(available, None) == "chat-7b"


def test_choose_falls_back_to_embedding_only_if_nothing_else() -> None:
    available = ["nomic-embed-text"]
    assert choose_chat_model(available, None) == "nomic-embed-text"


def test_choose_empty_list_returns_empty() -> None:
    assert choose_chat_model([], "anything") == ""


# --- refresh_chat_model (live-client orchestration) ---------------------------


class _FakeClient:
    """Minimal stand-in for LLMClient with controllable model list."""

    def __init__(self, *, reachable: bool, models: list[str], chat_model: str = "") -> None:
        self._reachable = reachable
        self._models = models
        self.chat_model = chat_model

    def health(self) -> HealthStatus:
        return HealthStatus(reachable=self._reachable)

    def list_models(self) -> list[str]:
        return list(self._models)


def _session_factory():
    engine = create_engine("sqlite://")  # shared in-memory for the connection's life
    Base.metadata.create_all(engine, tables=[Setting.__table__])

    def factory() -> Session:
        return Session(engine)

    return factory


def test_refresh_repairs_and_persists_when_model_gone() -> None:
    factory = _session_factory()
    client = _FakeClient(reachable=True, models=["small-7b", "big-32b"], chat_model="gone-13b")

    available = refresh_chat_model(client, factory)

    assert available == ["small-7b", "big-32b"]
    assert client.chat_model == "big-32b"
    with factory() as s:
        assert SettingsService(s).get("chat_model") == "big-32b"


def test_refresh_keeps_valid_choice_without_writing() -> None:
    factory = _session_factory()
    client = _FakeClient(reachable=True, models=["small-7b", "big-32b"], chat_model="small-7b")

    refresh_chat_model(client, factory)

    assert client.chat_model == "small-7b"
    with factory() as s:
        assert SettingsService(s).get("chat_model") is None  # nothing persisted


def test_refresh_noop_when_unreachable() -> None:
    factory = _session_factory()
    client = _FakeClient(reachable=False, models=[], chat_model="keep-me")

    assert refresh_chat_model(client, factory) is None
    assert client.chat_model == "keep-me"
