"""Pipeline runs are incremental: per-document commits, known feed items
skipped before download, and each source fetched since its last success."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from regwatch.db.models import Base, PipelineRun, UpdateEvent
from regwatch.domain.types import ExtractedDocument, MatchedDocument, RawDocument
from regwatch.pipeline.persist import persist_matched
from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.runner import PipelineRunner

PUBLISHED = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return engine


def _raw(url: str, *, published: datetime = PUBLISHED, source: str = "src") -> RawDocument:
    return RawDocument(
        source=source, source_url=url, title=url, published_at=published,
        raw_payload={}, fetched_at=datetime.now(UTC),
    )


def _extract(raw: RawDocument) -> ExtractedDocument:
    return ExtractedDocument(
        raw=raw, html_text=f"body of {raw.source_url} at {datetime.now(UTC)}",
        pdf_path=None, pdf_extracted_text=None, pdf_is_protected=False,
    )


def _match(extracted: ExtractedDocument) -> MatchedDocument:
    return MatchedDocument(extracted=extracted)


class _Source:
    name = "src"

    def __init__(self, docs: list[RawDocument], on_yield=None) -> None:  # type: ignore[no-untyped-def]
        self.docs = docs
        self.on_yield = on_yield
        self.since: datetime | None = None

    def fetch(self, since: datetime):  # type: ignore[no-untyped-def]
        self.since = since
        for i, doc in enumerate(self.docs):
            if self.on_yield:
                self.on_yield(i)
            yield doc


def test_each_document_is_committed_as_soon_as_it_is_stored(tmp_path: Path) -> None:
    """A run used to hold one transaction (and SQLite's write lock) for its
    whole 90 minutes; other writers failed with "database is locked"."""
    engine = _engine(tmp_path)
    visible: list[int] = []

    def _peek(i: int) -> None:
        with Session(engine) as other:
            visible.append(other.query(UpdateEvent).count())

    source = _Source([_raw("https://x/1"), _raw("https://x/2")], on_yield=_peek)
    with Session(engine) as session:
        PipelineRunner(session, sources=[source], extract=_extract, match=_match).run_once()

    assert visible == [0, 1]


def test_known_feed_item_is_skipped_before_download(tmp_path: Path) -> None:
    """Re-fetched pages differ cosmetically, so each run stored them again
    (128 URLs twice). Same source, URL and publication date = same item."""
    engine = _engine(tmp_path)
    extracted: list[str] = []

    def _counting_extract(raw: RawDocument) -> ExtractedDocument:
        extracted.append(raw.source_url)
        return _extract(raw)

    progress = PipelineProgress()
    with Session(engine) as session:
        PipelineRunner(
            session, sources=[_Source([_raw("https://x/1")])],
            extract=_counting_extract, match=_match,
        ).run_once()
        PipelineRunner(
            session,
            sources=[_Source([
                _raw("https://x/1"),
                _raw("https://x/1", published=PUBLISHED + timedelta(days=3)),
            ])],
            extract=_counting_extract, match=_match,
        ).run_once(progress=progress)

        assert session.query(UpdateEvent).count() == 2
    # First run, then only the republished item.
    assert extracted == ["https://x/1", "https://x/1"]
    assert progress.snapshot()["docs_skipped"] == 1


def test_each_source_is_fetched_since_its_last_successful_run(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    last_ok = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all([
            PipelineRun(started_at=last_ok, status="COMPLETED_WITH_ERRORS",
                        sources_attempted=["src", "flaky"], sources_failed=["flaky"]),
            PipelineRun(started_at=last_ok - timedelta(days=30), status="COMPLETED",
                        sources_attempted=["src", "flaky"], sources_failed=[]),
        ])
        session.commit()

        src, flaky, fresh = _Source([]), _Source([]), _Source([])
        flaky.name = "flaky"
        fresh.name = "fresh"
        PipelineRunner(
            session, sources=[src, flaky, fresh], extract=_extract, match=_match,
        ).run_once()

    assert src.since == last_ok - timedelta(days=14)
    assert flaky.since == last_ok - timedelta(days=44)
    assert fresh.since == datetime(2000, 1, 1, tzinfo=UTC)


def test_failed_document_does_not_poison_the_rest_of_the_run(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    calls = {"n": 0}

    def _flaky_persist(session: Session, matched: MatchedDocument):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            session.add(UpdateEvent(source="src"))  # NOT NULL violations on flush
            session.flush()
        return persist_matched(session, matched)

    source = _Source([_raw("https://x/1"), _raw("https://x/2")])
    with Session(engine) as session, patch(
        "regwatch.pipeline.runner.persist_matched", _flaky_persist
    ):
        run_id = PipelineRunner(
            session, sources=[source], extract=_extract, match=_match,
        ).run_once()

    with Session(engine) as session:
        urls = session.scalars(select(UpdateEvent.source_url)).all()
        run = session.get(PipelineRun, run_id)
        assert urls == ["https://x/2"]
        # The failed document marks the source as failed so it is retried.
        assert run.status == "COMPLETED_WITH_ERRORS"
        assert run.events_created == 1


def test_source_with_a_failed_document_is_not_counted_as_clean(tmp_path: Path) -> None:
    """A document that failed (e.g. a page timing out) must be retried by the
    next run; counting the source as successful moved its fetch window past
    the document for good."""
    engine = _engine(tmp_path)

    def _flaky_extract(raw: RawDocument) -> ExtractedDocument:
        if raw.source_url.endswith("/2"):
            raise TimeoutError("read timed out")
        return _extract(raw)

    with Session(engine) as session:
        run_id = PipelineRunner(
            session, sources=[_Source([_raw("https://x/1"), _raw("https://x/2")])],
            extract=_flaky_extract, match=_match,
        ).run_once()
        run = session.get(PipelineRun, run_id)
        assert run.sources_failed == ["src"]
        assert run.status == "COMPLETED_WITH_ERRORS"

        retry = _Source([_raw("https://x/1"), _raw("https://x/2")])
        PipelineRunner(session, sources=[retry], extract=_extract, match=_match).run_once()
        assert retry.since == datetime(2000, 1, 1, tzinfo=UTC)
        assert session.query(UpdateEvent).count() == 2
