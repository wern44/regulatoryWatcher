"""Auto-selection of the chat model from the models LM Studio/Ollama exposes.

The user picks a chat model from the Settings page and that choice is
persisted in the ``Setting`` table (key ``chat_model``).  This module fills
in / repairs that choice automatically:

* if nothing is chosen yet, or the chosen model is no longer served, pick a
  sensible default — the only model when there is exactly one, otherwise the
  largest by parameter count;
* a repaired choice is persisted, so it stays sticky until *that* model also
  disappears.

Only the chat model is auto-managed.  The embedding model is left under
manual control because swapping it can change the embedding dimension and
break the sqlite-vec virtual table.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from regwatch.llm.client import LLMClient

logger = logging.getLogger(__name__)

# Matches a parameter-count token in a model id, e.g. "7b", "14B", "0.5b",
# "1.5b" in "qwen2.5-7b-instruct".  The trailing boundary stops "7b" from
# also swallowing the "b" of a following word.
_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def estimate_param_billions(name: str) -> float:
    """Best-effort parameter count (in billions) parsed from a model id.

    Returns ``0.0`` when the id carries no recognisable ``<n>b`` token.  When
    several tokens are present (rare) the largest wins.
    """
    matches = _PARAM_RE.findall(name or "")
    if not matches:
        return 0.0
    return max(float(m) for m in matches)


def is_available(model: str, available: list[str]) -> bool:
    """Return True if *model* is served, matching :meth:`LLMClient._model_available`.

    Accepts an exact match or a prefix match against the part before an Ollama
    ``:tag`` separator, so a stored ``llama3`` still matches ``llama3:latest``.
    """
    if not model:
        return False
    if model in available:
        return True
    prefix = model.split(":")[0]
    return any(n.startswith(prefix) for n in available)


def _looks_like_embedding(name: str) -> bool:
    return "embed" in name.lower()


def choose_chat_model(available: list[str], current: str | None) -> str:
    """Pick the chat model to use given the *available* models.

    Keeps *current* if it is still served.  Otherwise selects the only model
    when there is exactly one, or the largest by ``(param_billions, name)``,
    preferring models that do not look like embedding models.  Returns ``""``
    when no models are available.
    """
    if current and is_available(current, available):
        return current
    if not available:
        return ""
    candidates = [m for m in available if not _looks_like_embedding(m)] or list(available)
    return max(candidates, key=lambda m: (estimate_param_billions(m), m))


def refresh_chat_model(
    llm_client: LLMClient,
    session_factory: Callable[[], AbstractContextManager[Session]],
) -> list[str] | None:
    """Auto-select/repair the chat model against the live server.

    Probes the server (``health`` + ``list_models``), recomputes the chat
    model with :func:`choose_chat_model`, and — when it differs from the
    client's current value — updates the live client and persists it to the
    ``Setting`` table so the repair survives restarts.

    Returns the list of available models, or ``None`` if the server could not
    be reached (in which case nothing is changed).
    """
    from regwatch.services.settings import SettingsService  # noqa: PLC0415

    try:
        # health() uses a short (5s) timeout and auto-detects the API format.
        # Gate list_models() — which uses the long per-call timeout — on it so
        # an unreachable server fails fast instead of blocking for minutes.
        if not llm_client.health().reachable:
            return None
        available = llm_client.list_models()
    except Exception:  # noqa: BLE001
        logger.warning("Could not list models for auto-selection; leaving model unchanged")
        return None

    if not isinstance(available, list):
        return None

    chosen = choose_chat_model(available, llm_client.chat_model)
    if chosen and chosen != llm_client.chat_model:
        logger.info(
            "Auto-selecting chat model %r (previous %r no longer available)",
            chosen,
            llm_client.chat_model,
        )
        llm_client.chat_model = chosen
        with session_factory() as session:
            SettingsService(session).set("chat_model", chosen)
            session.commit()
    return available
