"""Shared worker that drives DiscoveryService against AnalysisProgress.

Used by both POST /catalog/refresh (in a request-spawned thread) and the
scheduler's catalog-refresh job (in the APScheduler worker thread). Keeping
the two flows on the same code path means the user sees identical status
bar feedback and abort behaviour regardless of how the run was triggered.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from sqlalchemy.orm import Session

from regwatch.analysis.progress import AnalysisProgress
from regwatch.db.models import Regulation
from regwatch.llm.client import LLMClient
from regwatch.services.discovery import DiscoveryService

logger = logging.getLogger(__name__)


class _SessionFactory(Protocol):
    def __call__(self) -> AbstractContextManager[Session]: ...


def run_catalog_refresh(
    *,
    session_factory: _SessionFactory | Callable[[], AbstractContextManager[Session]],
    llm: LLMClient,
    auth_types: Sequence[str],
    progress: AnalysisProgress,
) -> None:
    """Run classify_catalog + discover_missing under a single AnalysisProgress.

    Initialises `progress` (start + tick), calls finish() with the appropriate
    terminal status (SUCCESS / ABORTED / FAILED). Any exception raised by
    `session_factory` or DiscoveryService is caught and recorded as FAILED.
    """
    try:
        with session_factory() as session:
            total = session.query(Regulation).count() + 1
        progress.start(run_id=0, total=total)
        progress.tick(0, total, "Starting catalog refresh…")

        with session_factory() as session:
            svc = DiscoveryService(session, llm=llm)
            svc.classify_catalog(progress=progress)
            if not progress.is_cancel_requested:
                svc.discover_missing(list(auth_types), progress=progress)
            session.commit()

        if progress.is_cancel_requested:
            progress.finish("ABORTED")
        else:
            progress.finish("SUCCESS")
    except Exception as e:  # noqa: BLE001
        logger.exception("Catalog refresh failed")
        # `progress.start` may not have been called yet if session_factory raised.
        if progress.status != "running":
            progress.start(run_id=0, total=0)
        progress.finish("FAILED", error=str(e))
