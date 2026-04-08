"""Pipeline runner: orchestrates Fetch -> Extract -> Match -> Persist -> Notify."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from regwatch.db.models import PipelineRun
from regwatch.domain.types import ExtractedDocument, MatchedDocument, RawDocument
from regwatch.pipeline.persist import persist_matched

logger = logging.getLogger(__name__)

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

    def run_once(self, since: datetime | None = None) -> int:
        """Run all sources once. Returns the pipeline_run id."""
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
        self._session.flush()

        since = since or datetime(2000, 1, 1, tzinfo=UTC)

        for source in self._sources:
            run.sources_attempted = [*run.sources_attempted, source.name]
            try:
                for raw in source.fetch(since):
                    try:
                        extracted = self._extract(raw)
                        matched = self._match(extracted)
                        result = persist_matched(self._session, matched)
                        run.events_created += result.events_created
                        run.versions_created += result.versions_created
                    except Exception:  # noqa: BLE001
                        logger.exception("Per-document failure in %s", source.name)
            except Exception:  # noqa: BLE001
                logger.exception("Source %s failed", source.name)
                run.sources_failed = [*run.sources_failed, source.name]

        run.finished_at = datetime.now(UTC)
        run.status = "COMPLETED"
        self._session.flush()
        return run.run_id

    def _abort_stale_runs(self) -> None:
        self._session.execute(
            update(PipelineRun)
            .where(PipelineRun.status == "RUNNING")
            .values(status="ABORTED", finished_at=datetime.now(UTC))
        )
