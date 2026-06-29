"""Shared pipeline execution logic for manual and scheduled runs."""
from __future__ import annotations

import logging

from regwatch.pipeline.pipeline_factory import build_runner
from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.sources import build_enabled_sources
from regwatch.services.runtime_limits import get_max_runtime_seconds, runtime_watchdog

logger = logging.getLogger(__name__)


def run_pipeline_background(
    *,
    session_factory,
    config,
    llm_client,
    progress: PipelineProgress,
    source_names: list[str] | None = None,
    entity_type_prompt: str | None = None,
) -> None:
    """Run all enabled sources in a fresh DB session.

    Used by both the manual "Run pipeline now" button and the scheduler.
    Catches all exceptions and reports them via *progress*.
    If *source_names* is set, only those sources are run.
    *entity_type_prompt* is the cached LLM-facing entity-type bullet list,
    typically read from ``app.state.entity_type_prompt``.
    """
    try:
        sources = build_enabled_sources(config, only=source_names)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline source instantiation failed")
        progress.finish(run_id=None, error=f"{type(exc).__name__}: {exc}")
        return

    with session_factory() as session:
        max_seconds = get_max_runtime_seconds(session, config, "pipeline")
        try:
            runner = build_runner(
                session,
                sources=sources,
                archive_root=config.paths.pdf_archive,
                llm_client=llm_client,
                entity_type_prompt=entity_type_prompt,
            )
            with runtime_watchdog(progress, max_seconds, label="Pipeline run") as watch:
                run_id = runner.run_once(progress=progress)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("Pipeline run failed")
            progress.finish(run_id=None, error=f"{type(exc).__name__}: {exc}")
            return

    if watch.timed_out:
        progress.finish(
            run_id=run_id,
            aborted=True,
            aborted_message=f"Aborted — exceeded the maximum runtime of {max_seconds}s.",
        )
    else:
        progress.finish(run_id=run_id, aborted=progress.is_cancel_requested)
