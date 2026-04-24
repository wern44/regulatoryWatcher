# tests/unit/test_scheduler_jobs.py
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from regwatch.scheduler.jobs import FREQUENCY_OPTIONS, SchedulerManager


@pytest.fixture()
def manager():
    scheduler = BackgroundScheduler(timezone="UTC")
    mgr = SchedulerManager(
        scheduler=scheduler,
        run_fn=MagicMock(),
    )
    scheduler.start()
    yield mgr
    if scheduler.running:
        scheduler.shutdown(wait=False)


def test_apply_schedule_creates_job(manager: SchedulerManager):
    manager.apply_schedule("daily", "08:00")
    job = manager._scheduler.get_job(SchedulerManager.JOB_ID)
    assert job is not None


def test_apply_schedule_replaces_existing_job(manager: SchedulerManager):
    manager.apply_schedule("daily", "08:00")
    manager.apply_schedule("weekly", "10:00")
    jobs = manager._scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == SchedulerManager.JOB_ID


def test_pause_and_resume(manager: SchedulerManager):
    manager.apply_schedule("daily", "08:00")
    manager.pause()
    assert manager.next_run_time() is None  # paused job has no next_run_time
    manager.resume()
    assert manager.next_run_time() is not None


def test_next_run_time_returns_none_when_no_job(manager: SchedulerManager):
    assert manager.next_run_time() is None


def test_next_run_time_returns_datetime_when_scheduled(
    manager: SchedulerManager,
):
    manager.apply_schedule("daily", "06:00")
    nrt = manager.next_run_time()
    assert isinstance(nrt, datetime)


def test_4h_frequency_uses_interval_trigger(manager: SchedulerManager):
    manager.apply_schedule("4h", "00:00")
    job = manager._scheduler.get_job(SchedulerManager.JOB_ID)
    assert job is not None
    assert "interval" in str(type(job.trigger)).lower()


def test_daily_frequency_uses_cron_trigger(manager: SchedulerManager):
    manager.apply_schedule("daily", "14:30")
    job = manager._scheduler.get_job(SchedulerManager.JOB_ID)
    assert "cron" in str(type(job.trigger)).lower()


def test_frequency_options_has_all_keys():
    assert set(FREQUENCY_OPTIONS.keys()) == {"4h", "daily", "2days", "weekly", "monthly"}


def test_is_running_false_when_no_job(manager: SchedulerManager):
    assert manager.is_running() is False


def test_is_running_true_after_apply(manager: SchedulerManager):
    manager.apply_schedule("daily", "06:00")
    assert manager.is_running() is True


def test_is_running_false_after_pause(manager: SchedulerManager):
    manager.apply_schedule("daily", "06:00")
    manager.pause()
    assert manager.is_running() is False
