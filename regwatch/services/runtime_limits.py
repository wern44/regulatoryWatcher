"""Wall-clock runtime limits for long-running LLM processes.

Both the pipeline run and the catalog-refresh/analysis run already support
*cooperative* cancellation: a worker thread checks a cancel flag between
documents/phases and stops cleanly at the next boundary. This module adds a
watchdog that trips that same flag once a configurable deadline passes, plus a
helper that resolves the effective limit from the ``Setting`` table (user
override) falling back to the config default.

The abort is cooperative by design — it fires at the next item boundary, not
mid-LLM-call. A single call is independently bounded by the client's
``llm_call_timeout_seconds``, so the worst-case overrun past the deadline is
roughly one item's processing time.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from regwatch.config import AppConfig

logger = logging.getLogger(__name__)

# kind -> (Setting key, AnalysisConfig attribute holding the default)
_LIMITS = {
    "pipeline": ("pipeline_max_runtime_seconds", "max_pipeline_runtime_seconds"),
    "analysis": ("analysis_max_runtime_seconds", "max_analysis_runtime_seconds"),
}


def get_max_runtime_seconds(session: Session, config: AppConfig, kind: str) -> int:
    """Resolve the effective max runtime (seconds) for *kind*.

    Precedence: the ``Setting`` row (user override) when present and valid,
    otherwise the config default. ``0`` means unlimited. Unknown *kind* or a
    malformed stored value falls back to the config default (or ``0``).
    """
    from regwatch.services.settings import SettingsService  # noqa: PLC0415

    setting_key, config_attr = _LIMITS.get(kind, ("", ""))
    try:
        default = int(getattr(config.analysis, config_attr, 0) or 0) if config_attr else 0
    except (TypeError, ValueError):
        default = 0
    if not setting_key:
        return max(0, default)
    raw = SettingsService(session).get(setting_key)
    if raw is None or raw.strip() == "":
        return max(0, default)
    try:
        return max(0, int(raw))
    except ValueError:
        return max(0, default)


class _Cancellable(Protocol):
    status: str

    @property
    def is_cancel_requested(self) -> bool: ...

    def request_cancel(self) -> None: ...


@dataclass
class WatchdogHandle:
    """Returned by :func:`runtime_watchdog`; ``timed_out`` flips True if it fired."""

    timed_out: bool = False


@contextmanager
def runtime_watchdog(
    progress: _Cancellable | None,
    seconds: int,
    *,
    label: str,
) -> Iterator[WatchdogHandle]:
    """Request a cooperative cancel on *progress* after *seconds* elapse.

    A no-op when *seconds* <= 0 or *progress* is None. The timer is always
    cancelled on exit, so a run that finishes before the deadline never trips.
    """
    handle = WatchdogHandle()
    timer: threading.Timer | None = None

    if seconds and seconds > 0 and progress is not None:
        def _fire() -> None:
            if getattr(progress, "status", "") == "running" and not progress.is_cancel_requested:
                handle.timed_out = True
                logger.warning(
                    "%s exceeded its maximum runtime of %ss — requesting abort",
                    label,
                    seconds,
                )
                progress.request_cancel()

        timer = threading.Timer(float(seconds), _fire)
        timer.daemon = True
        timer.start()

    try:
        yield handle
    finally:
        if timer is not None:
            timer.cancel()
