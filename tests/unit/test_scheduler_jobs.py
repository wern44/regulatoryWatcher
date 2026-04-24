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
        pipeline_fn=MagicMock(),
        reconciliation_fn=MagicMock(),
    )
    scheduler.start()
    yield mgr
    if scheduler.running:
        scheduler.shutdown(wait=False)


def test_apply_schedule_creates_pipeline_job(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert job is not None


def test_apply_schedule_creates_reconciliation_job(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.RECONCILIATION_JOB_ID, "weekly", "05:00")
    job = manager._scheduler.get_job(SchedulerManager.RECONCILIATION_JOB_ID)
    assert job is not None


def test_two_jobs_coexist(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.apply_schedule(SchedulerManager.RECONCILIATION_JOB_ID, "weekly", "05:00")
    jobs = manager._scheduler.get_jobs()
    assert len(jobs) == 2
    job_ids = {j.id for j in jobs}
    assert SchedulerManager.PIPELINE_JOB_ID in job_ids
    assert SchedulerManager.RECONCILIATION_JOB_ID in job_ids


def test_apply_schedule_replaces_existing_job(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "weekly", "10:00")
    jobs = [j for j in manager._scheduler.get_jobs() if j.id == SchedulerManager.PIPELINE_JOB_ID]
    assert len(jobs) == 1


def test_pause_and_resume(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None
    manager.resume(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is not None


def test_pause_one_job_doesnt_affect_other(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.apply_schedule(SchedulerManager.RECONCILIATION_JOB_ID, "weekly", "05:00")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None
    assert manager.next_run_time(SchedulerManager.RECONCILIATION_JOB_ID) is not None


def test_next_run_time_returns_none_when_no_job(manager: SchedulerManager):
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None


def test_next_run_time_returns_datetime_when_scheduled(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    nrt = manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID)
    assert isinstance(nrt, datetime)


def test_4h_frequency_uses_interval_trigger(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "4h", "00:00")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert "interval" in str(type(job.trigger)).lower()


def test_daily_frequency_uses_cron_trigger(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "14:30")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert "cron" in str(type(job.trigger)).lower()


def test_frequency_options_has_all_keys():
    assert set(FREQUENCY_OPTIONS.keys()) == {"4h", "daily", "2days", "weekly", "monthly"}


def test_is_running_false_when_no_job(manager: SchedulerManager):
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is False


def test_is_running_true_after_apply(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is True


def test_is_running_false_after_pause(manager: SchedulerManager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is False
