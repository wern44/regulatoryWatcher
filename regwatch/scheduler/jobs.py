"""APScheduler-based pipeline scheduler.

A single ``SchedulerManager`` wraps a ``BackgroundScheduler`` and exposes
apply / pause / resume controls.  It manages exactly one job whose trigger
is derived from the user-chosen frequency string.
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
    """Manages a single scheduled pipeline job."""

    JOB_ID = "scheduled_pipeline_run"

    def __init__(
        self,
        *,
        scheduler: BackgroundScheduler,
        run_fn: Callable[[], None],
    ) -> None:
        self._scheduler = scheduler
        self._run_fn = run_fn
        self._timezone: str = str(scheduler.timezone)

    def apply_schedule(self, frequency: str, time_str: str) -> None:
        """Remove any existing job and add a new one with the given trigger."""
        existing = self._scheduler.get_job(self.JOB_ID)
        if existing is not None:
            self._scheduler.remove_job(self.JOB_ID)

        trigger = _build_trigger(frequency, time_str, self._timezone)
        self._scheduler.add_job(
            self._run_fn,
            trigger=trigger,
            id=self.JOB_ID,
            name="Scheduled pipeline run",
            max_instances=1,
            replace_existing=True,
        )
        logger.info(
            "Scheduled pipeline: frequency=%s, time=%s", frequency, time_str
        )

    def pause(self) -> None:
        """Pause the scheduled job (it stays registered but won't fire)."""
        if self._scheduler.get_job(self.JOB_ID) is not None:
            self._scheduler.pause_job(self.JOB_ID)
            logger.info("Scheduler paused")

    def resume(self) -> None:
        """Resume a paused job."""
        if self._scheduler.get_job(self.JOB_ID) is not None:
            self._scheduler.resume_job(self.JOB_ID)
            logger.info("Scheduler resumed")

    def next_run_time(self) -> datetime | None:
        """Return the next fire time, or None if paused/no job."""
        job = self._scheduler.get_job(self.JOB_ID)
        if job is None:
            return None
        return job.next_run_time

    def is_running(self) -> bool:
        """True if the scheduler is started and the job is active (not paused)."""
        job = self._scheduler.get_job(self.JOB_ID)
        if job is None:
            return False
        return job.next_run_time is not None
