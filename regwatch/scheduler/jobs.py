"""APScheduler configuration: source-to-job mapping and job builder."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from regwatch.config import AppConfig

# Maps each source name to the logical job it runs in.
SOURCE_TO_JOB: dict[str, str] = {
    "cssf_rss": "run_pipeline_cssf",
    "cssf_consultation": "run_pipeline_cssf",
    "eur_lex_adopted": "run_pipeline_eu",
    "eur_lex_proposal": "run_pipeline_eu",
    "legilux_sparql": "run_pipeline_lu",
    "legilux_parliamentary": "run_pipeline_lu",
    "esma_rss": "run_pipeline_esma_eba_fisma",
    "eba_rss": "run_pipeline_esma_eba_fisma",
    "ec_fisma_rss": "run_pipeline_esma_eba_fisma",
}


def assert_sources_have_jobs(config: AppConfig) -> None:
    for name, source_cfg in config.sources.items():
        if source_cfg.enabled and name not in SOURCE_TO_JOB:
            raise ValueError(
                f"Enabled source {name!r} has no job mapping in SOURCE_TO_JOB. "
                "Register it before starting the scheduler."
            )


def build_scheduler(
    config: AppConfig,
    *,
    run_pipeline_for: Callable[[list[str]], Any],
    start: bool = True,
) -> BackgroundScheduler:
    """Create an APScheduler with one job per active pipeline group.

    `run_pipeline_for(source_names)` is the callback that the scheduler invokes.
    """
    assert_sources_have_jobs(config)

    scheduler = BackgroundScheduler(timezone=config.ui.timezone)

    grouped: dict[str, list[str]] = {}
    grouped_interval: dict[str, int] = {}
    for source_name, source_cfg in config.sources.items():
        if not source_cfg.enabled:
            continue
        job_name = SOURCE_TO_JOB[source_name]
        grouped.setdefault(job_name, []).append(source_name)
        # Use the minimum interval of any source in the group.
        prev = grouped_interval.get(job_name)
        if prev is None or source_cfg.interval_hours < prev:
            grouped_interval[job_name] = source_cfg.interval_hours

    for job_name, sources in grouped.items():
        scheduler.add_job(
            run_pipeline_for,
            trigger=IntervalTrigger(hours=grouped_interval[job_name]),
            id=job_name,
            name=job_name,
            args=(sources,),
            replace_existing=True,
        )

    if start:
        scheduler.start()
    return scheduler
