"""Pipeline runner: orchestrates Fetch -> Extract -> Match -> Persist -> Notify."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from regwatch.db.models import PipelineRun, UpdateEvent
from regwatch.domain.types import ExtractedDocument, MatchedDocument, RawDocument
from regwatch.pipeline.hashing import content_hash, text_for_hashing
from regwatch.pipeline.persist import persist_matched
from regwatch.pipeline.progress import PipelineProgress

logger = logging.getLogger(__name__)

# A source is fetched from its last successful run minus this margin, to
# catch items published (or backdated) while that run was in progress.
_LOOKBACK = timedelta(days=14)
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)

ExtractFn = Callable[[RawDocument], ExtractedDocument]
MatchFn = Callable[[ExtractedDocument], MatchedDocument]


@dataclass
class SourceFailure:
    source_name: str
    error: str


class PipelineRunner:
    def __init__(
        self,
        session: Session,
        *,
        sources: Iterable,
        extract: ExtractFn,
        match: MatchFn,
    ) -> None:
        self._session = session
        self._sources = list(sources)
        self._extract = extract
        self._match = match

    def run_once(
        self,
        since: datetime | None = None,
        *,
        progress: PipelineProgress | None = None,
    ) -> int:
        """Run all sources once. Returns the pipeline_run id.

        Each source is fetched since its last successful run (see
        ``_since_for``) unless ``since`` is given. Every stored document is
        committed on its own, so the run never holds SQLite's write lock for
        long and a failed document is rolled back without affecting the rest.

        If `progress` is given, the runner reports milestones to it (source
        start/fail, document fetched, persist result). The progress object
        is thread-safe so a polling reader can call `snapshot()` from a
        different thread without seeing torn state.
        """
        self._abort_stale_runs()
        run = PipelineRun(
            started_at=datetime.now(UTC),
            status="RUNNING",
            sources_attempted=[],
            sources_failed=[],
            events_created=0,
            versions_created=0,
        )
        self._session.add(run)
        self._session.commit()

        pre_cancel = progress is not None and progress.is_cancel_requested
        if progress is not None:
            progress.reset_for_run(total_sources=len(self._sources))
        if pre_cancel and progress is not None:
            progress.request_cancel()

        aborted = False

        def _cancelled() -> bool:
            return progress is not None and progress.is_cancel_requested

        for idx, source in enumerate(self._sources, start=1):
            if _cancelled():
                aborted = True
                break
            if progress is not None:
                progress.begin_source(source.name, idx)
            source_since = since or self._since_for(source.name)
            run.sources_attempted = [*run.sources_attempted, source.name]
            self._session.commit()
            try:
                for raw in source.fetch(source_since):
                    if _cancelled():
                        aborted = True
                        break
                    if progress is not None:
                        progress.begin_document(raw.title or raw.source_url)
                    if self._is_known_item(raw):
                        # Same feed item as before; re-fetched pages differ
                        # cosmetically and would be stored again.
                        if progress is not None:
                            progress.note_skipped()
                        continue
                    try:
                        extracted = self._extract(raw)
                        text_hash = content_hash(text_for_hashing(extracted))
                        already_seen = self._session.scalar(
                            select(UpdateEvent.event_id).where(
                                UpdateEvent.content_hash == text_hash
                            )
                        )
                        if already_seen is not None:
                            if progress is not None:
                                progress.note_skipped()
                            continue
                        if progress is not None:
                            progress.set_phase("MATCH")
                        matched = self._match(extracted)
                        if progress is not None:
                            progress.set_phase("PERSIST")
                        result = persist_matched(self._session, matched)
                        run.events_created += result.events_created
                        run.versions_created += result.versions_created
                        self._session.commit()
                        if progress is not None:
                            progress.add_persist_result(
                                result.events_created, result.versions_created
                            )
                    except Exception:  # noqa: BLE001
                        self._session.rollback()
                        logger.exception("Per-document failure in %s", source.name)
                        # Not a clean run for this source: the next run must
                        # look back far enough to retry the document.
                        if source.name not in run.sources_failed:
                            run.sources_failed = [*run.sources_failed, source.name]
                            self._session.commit()
                if aborted:
                    break
            except Exception:  # noqa: BLE001
                self._session.rollback()
                logger.exception("Source %s failed", source.name)
                run.sources_failed = [*run.sources_failed, source.name]
                if progress is not None:
                    progress.fail_source(source.name)

        run.finished_at = datetime.now(UTC)
        if aborted:
            run.status = "ABORTED"
        else:
            run.status = (
                "COMPLETED_WITH_ERRORS" if run.sources_failed else "COMPLETED"
            )
        self._session.commit()
        return run.run_id

    def _since_for(self, source_name: str) -> datetime:
        """Start of the fetch window: the source's last successful run
        (attempted and not failed) minus ``_LOOKBACK``; the epoch if none."""
        runs = self._session.scalars(
            select(PipelineRun)
            .where(PipelineRun.status.in_(["COMPLETED", "COMPLETED_WITH_ERRORS"]))
            .order_by(PipelineRun.started_at.desc())
        )
        for past in runs:
            if source_name in (past.sources_attempted or []) and source_name not in (
                past.sources_failed or []
            ):
                return past.started_at - _LOOKBACK
        return _EPOCH

    def _is_known_item(self, raw: RawDocument) -> bool:
        """An event exists for this source, URL and publication date."""
        return self._session.scalar(
            select(UpdateEvent.event_id)
            .where(
                UpdateEvent.source == raw.source,
                UpdateEvent.source_url == raw.source_url,
                UpdateEvent.published_at == raw.published_at,
            )
            .limit(1)
        ) is not None

    def _abort_stale_runs(self) -> None:
        self._session.execute(
            update(PipelineRun)
            .where(PipelineRun.status == "RUNNING")
            .values(status="ABORTED", finished_at=datetime.now(UTC))
        )
