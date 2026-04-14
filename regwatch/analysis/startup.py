"""Startup sweep to finalize AnalysisRun rows left in RUNNING by a prior crash."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from regwatch.db.models import AnalysisRun, AnalysisRunStatus

logger = logging.getLogger(__name__)


def sweep_stuck_runs(session: Session, *, threshold_minutes: int = 10) -> int:
    """Mark RUNNING analysis runs whose `started_at` is older than `threshold_minutes` as FAILED.

    Returns the number of rows updated. Does NOT commit — caller commits.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=threshold_minutes)
    stale = (
        session.query(AnalysisRun)
        .filter(AnalysisRun.status == AnalysisRunStatus.RUNNING)
        .all()
    )
    updated = 0
    for run in stale:
        started = run.started_at
        if started is not None and started > cutoff:
            continue  # still fresh, belongs to an in-flight worker
        run.status = AnalysisRunStatus.FAILED
        run.finished_at = datetime.now(UTC)
        existing = run.error_summary or ""
        run.error_summary = (
            "interrupted before completion"
            if not existing else f"{existing}\ninterrupted before completion"
        )
        updated += 1
    if updated:
        logger.info("Swept %d stuck AnalysisRun row(s) to FAILED", updated)
    session.flush()
    return updated
