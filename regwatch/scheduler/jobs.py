"""APScheduler-based pipeline scheduler.

A single ``SchedulerManager`` wraps a ``BackgroundScheduler`` and exposes
apply / pause / resume controls.  It manages named jobs whose triggers
are derived from user-chosen frequency strings.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Maps the DB value to a human-readable label shown in the UI.
FREQUENCY_OPTIONS: dict[str, str] = {
    "4h": "Every 4 hours",
    "daily": "Daily",
    "2days": "Every 2 days",
    "weekly": "Weekly",
    "monthly": "Monthly",
}


def _build_trigger(
    frequency: str, time_str: str, timezone: str
) -> IntervalTrigger | CronTrigger:
    """Return the APScheduler trigger for *frequency* and *time_str* (HH:MM)."""
    hour, minute = (int(p) for p in time_str.split(":"))
    if frequency == "4h":
        return IntervalTrigger(hours=4, timezone=timezone)
    if frequency == "daily":
        return CronTrigger(hour=hour, minute=minute, timezone=timezone)
    if frequency == "2days":
        return CronTrigger(
            hour=hour, minute=minute, day="*/2", timezone=timezone
        )
    if frequency == "weekly":
        return CronTrigger(
            day_of_week="mon", hour=hour, minute=minute, timezone=timezone
        )
    if frequency == "monthly":
        return CronTrigger(
            day=1, hour=hour, minute=minute, timezone=timezone
        )
    raise ValueError(f"Unknown frequency: {frequency!r}")


class SchedulerManager:
    """Manages named scheduled jobs (pipeline + reconciliation)."""

    PIPELINE_JOB_ID = "scheduled_pipeline_run"
    RECONCILIATION_JOB_ID = "scheduled_reconciliation"

    def __init__(
        self,
        *,
        scheduler: BackgroundScheduler,
        pipeline_fn: Callable[[], None],
        reconciliation_fn: Callable[[], None],
    ) -> None:
        self._scheduler = scheduler
        self._fns: dict[str, Callable[[], None]] = {
            self.PIPELINE_JOB_ID: pipeline_fn,
            self.RECONCILIATION_JOB_ID: reconciliation_fn,
        }
        self._timezone: str = str(scheduler.timezone)

    def apply_schedule(
        self, job_id: str, frequency: str, time_str: str
    ) -> None:
        """Remove any existing job and add a new one with the given trigger."""
        existing = self._scheduler.get_job(job_id)
        if existing is not None:
            self._scheduler.remove_job(job_id)

        trigger = _build_trigger(frequency, time_str, self._timezone)
        self._scheduler.add_job(
            self._fns[job_id],
            trigger=trigger,
            id=job_id,
            name=job_id.replace("_", " ").title(),
            max_instances=1,
            replace_existing=True,
        )
        logger.info(
            "Scheduled %s: frequency=%s, time=%s", job_id, frequency, time_str
        )

    def pause(self, job_id: str) -> None:
        """Pause a scheduled job (it stays registered but won't fire)."""
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.pause_job(job_id)
            logger.info("Job %s paused", job_id)

    def resume(self, job_id: str) -> None:
        """Resume a paused job."""
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.resume_job(job_id)
            logger.info("Job %s resumed", job_id)

    def next_run_time(self, job_id: str) -> datetime | None:
        """Return the next fire time, or None if paused/no job."""
        job = self._scheduler.get_job(job_id)
        if job is None:
            return None
        return job.next_run_time

    def is_running(self, job_id: str) -> bool:
        """True if the job is registered and active (not paused)."""
        job = self._scheduler.get_job(job_id)
        if job is None:
            return False
        return job.next_run_time is not None
