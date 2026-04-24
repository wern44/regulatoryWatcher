"""Shared pipeline execution logic for manual and scheduled runs."""
from __future__ import annotations

import logging

from regwatch.pipeline.pipeline_factory import build_runner
from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.sources import build_enabled_sources

logger = logging.getLogger(__name__)


def run_pipeline_background(
    *,
    session_factory,
    config,
    llm_client,
    progress: PipelineProgress,
) -> None:
    """Run all enabled sources in a fresh DB session.

    Used by both the manual "Run pipeline now" button and the scheduler.
    Catches all exceptions and reports them via *progress*.
    """
    try:
        sources = build_enabled_sources(config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline source instantiation failed")
        progress.finish(run_id=None, error=f"{type(exc).__name__}: {exc}")
        return

    with session_factory() as session:
        try:
            runner = build_runner(
                session,
                sources=sources,
                archive_root=config.paths.pdf_archive,
                llm_client=llm_client,
            )
            run_id = runner.run_once(progress=progress)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("Pipeline run failed")
            progress.finish(run_id=None, error=f"{type(exc).__name__}: {exc}")
            return

    progress.finish(run_id=run_id)
