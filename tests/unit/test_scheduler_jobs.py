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
        jobs={
            SchedulerManager.PIPELINE_JOB_ID: MagicMock(),
            SchedulerManager.DISCOVERY_JOB_ID: MagicMock(),
            SchedulerManager.RECONCILIATION_JOB_ID: MagicMock(),
            SchedulerManager.ANALYSIS_JOB_ID: MagicMock(),
        },
    )
    scheduler.start()
    yield mgr
    if scheduler.running:
        scheduler.shutdown(wait=False)


def test_all_four_job_ids_exist():
    assert hasattr(SchedulerManager, "PIPELINE_JOB_ID")
    assert hasattr(SchedulerManager, "DISCOVERY_JOB_ID")
    assert hasattr(SchedulerManager, "RECONCILIATION_JOB_ID")
    assert hasattr(SchedulerManager, "ANALYSIS_JOB_ID")


def test_four_jobs_coexist(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    manager.apply_schedule(SchedulerManager.DISCOVERY_JOB_ID, "weekly", "05:30")
    manager.apply_schedule(SchedulerManager.RECONCILIATION_JOB_ID, "weekly", "05:00")
    manager.apply_schedule(SchedulerManager.ANALYSIS_JOB_ID, "monthly", "04:00")
    assert len(manager._scheduler.get_jobs()) == 4


def test_apply_schedule_creates_job(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    assert manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID) is not None


def test_apply_schedule_replaces_existing(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "weekly", "10:00")
    jobs = [j for j in manager._scheduler.get_jobs() if j.id == SchedulerManager.PIPELINE_JOB_ID]
    assert len(jobs) == 1


def test_pause_and_resume(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None
    manager.resume(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is not None


def test_pause_one_doesnt_affect_other(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "08:00")
    manager.apply_schedule(SchedulerManager.DISCOVERY_JOB_ID, "weekly", "05:30")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None
    assert manager.next_run_time(SchedulerManager.DISCOVERY_JOB_ID) is not None


def test_next_run_time_none_when_no_job(manager):
    assert manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID) is None


def test_next_run_time_datetime_when_scheduled(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    assert isinstance(manager.next_run_time(SchedulerManager.PIPELINE_JOB_ID), datetime)


def test_4h_uses_interval(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "4h", "00:00")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert "interval" in str(type(job.trigger)).lower()


def test_daily_uses_cron(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "14:30")
    job = manager._scheduler.get_job(SchedulerManager.PIPELINE_JOB_ID)
    assert "cron" in str(type(job.trigger)).lower()


def test_frequency_options_keys():
    assert set(FREQUENCY_OPTIONS.keys()) == {"4h", "daily", "2days", "weekly", "monthly"}


def test_is_running_false_no_job(manager):
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is False


def test_is_running_true_after_apply(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is True


def test_is_running_false_after_pause(manager):
    manager.apply_schedule(SchedulerManager.PIPELINE_JOB_ID, "daily", "06:00")
    manager.pause(SchedulerManager.PIPELINE_JOB_ID)
    assert manager.is_running(SchedulerManager.PIPELINE_JOB_ID) is False
