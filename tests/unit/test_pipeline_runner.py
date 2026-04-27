from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import Base, PipelineRun, UpdateEvent
from regwatch.domain.types import ExtractedDocument, MatchedDocument, RawDocument
from regwatch.pipeline.hashing import content_hash
from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.runner import PipelineRunner


def test_run_sets_completed_with_errors_when_source_fails(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    class FailingSource:
        name = "fail_source"
        def fetch(self, since):
            raise RuntimeError("boom")

    class OkSource:
        name = "ok_source"
        def fetch(self, since):
            return iter([])

    with Session(engine) as session:
        runner = PipelineRunner(
            session,
            sources=[OkSource(), FailingSource()],
            extract=MagicMock(),
            match=MagicMock(),
        )
        run_id = runner.run_once()
        session.commit()
        run = session.get(PipelineRun, run_id)
        assert run.status == "COMPLETED_WITH_ERRORS"
        assert "fail_source" in run.sources_failed


def test_run_sets_completed_when_all_sources_ok(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    class OkSource:
        name = "ok_source"
        def fetch(self, since):
            return iter([])

    with Session(engine) as session:
        runner = PipelineRunner(
            session,
            sources=[OkSource()],
            extract=MagicMock(),
            match=MagicMock(),
        )
        run_id = runner.run_once()
        session.commit()
        run = session.get(PipelineRun, run_id)
        assert run.status == "COMPLETED"
        assert run.sources_failed == []


def _raw_doc() -> RawDocument:
    now = datetime.now(UTC)
    return RawDocument(
        source="cssf_rss",
        source_url="https://example.com/dup",
        title="dup",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )


def test_runner_skips_match_when_hash_already_in_update_event(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    raw = _raw_doc()
    body = "the body that is already in the catalog"
    pre_existing_hash = content_hash(body)

    class OneDocSource:
        name = "src_one"

        def fetch(self, since):
            return iter([raw])

    def fake_extract(r: RawDocument) -> ExtractedDocument:
        return ExtractedDocument(
            raw=r,
            html_text=body,
            pdf_path=None,
            pdf_extracted_text=None,
            pdf_is_protected=False,
        )

    match_calls: list[ExtractedDocument] = []

    def fake_match(extracted: ExtractedDocument) -> MatchedDocument:
        match_calls.append(extracted)
        return MatchedDocument(extracted=extracted)

    with Session(engine) as session:
        session.add(
            UpdateEvent(
                source="prior_run",
                source_url="https://example.com/dup-prior",
                title="prior",
                published_at=datetime.now(UTC),
                fetched_at=datetime.now(UTC),
                raw_payload={},
                content_hash=pre_existing_hash,
                is_ict=False,
                severity="INFORMATIONAL",
                review_status="NEW",
            )
        )
        session.flush()

        progress = PipelineProgress()
        progress.reset_for_run(total_sources=1)

        runner = PipelineRunner(
            session,
            sources=[OneDocSource()],
            extract=fake_extract,
            match=fake_match,
        )
        runner.run_once(progress=progress)
        session.commit()

    assert match_calls == []  # match never invoked for the duplicate
    assert progress.snapshot()["docs_skipped"] == 1
    assert progress.snapshot()["docs_seen"] == 1


def test_runner_aborts_between_documents(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    progress = PipelineProgress()
    progress.reset_for_run(total_sources=1)

    raws = [
        RawDocument(
            source="src",
            source_url=f"https://example.com/{i}",
            title=f"doc {i}",
            published_at=datetime.now(UTC),
            raw_payload={},
            fetched_at=datetime.now(UTC),
        )
        for i in range(3)
    ]

    class ThreeDocSource:
        name = "src"

        def fetch(self, since):
            return iter(raws)

    extract_calls: list[RawDocument] = []

    def fake_extract(r: RawDocument) -> ExtractedDocument:
        extract_calls.append(r)
        # Trigger the abort after the first doc has gone through extract+match.
        if len(extract_calls) == 1:
            progress.request_cancel()
        return ExtractedDocument(
            raw=r,
            html_text=f"text {len(extract_calls)}",  # unique text -> not deduped
            pdf_path=None,
            pdf_extracted_text=None,
            pdf_is_protected=False,
        )

    def fake_match(extracted: ExtractedDocument) -> MatchedDocument:
        return MatchedDocument(extracted=extracted)

    with Session(engine) as session:
        runner = PipelineRunner(
            session,
            sources=[ThreeDocSource()],
            extract=fake_extract,
            match=fake_match,
        )
        run_id = runner.run_once(progress=progress)
        session.commit()

        run = session.get(PipelineRun, run_id)
        assert run.status == "ABORTED"
        # First doc completed; the cancel was set during its extract,
        # so the next iteration of the doc loop sees the flag and stops.
        assert len(extract_calls) == 1


def test_runner_aborts_between_sources(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)

    progress = PipelineProgress()
    progress.reset_for_run(total_sources=2)
    progress.request_cancel()  # already cancelled before the run starts

    fetched_from: list[str] = []

    class S1:
        name = "s1"

        def fetch(self, since):
            fetched_from.append("s1")
            return iter([])

    class S2:
        name = "s2"

        def fetch(self, since):
            fetched_from.append("s2")
            return iter([])

    with Session(engine) as session:
        runner = PipelineRunner(
            session,
            sources=[S1(), S2()],
            extract=MagicMock(),
            match=MagicMock(),
        )
        run_id = runner.run_once(progress=progress)
        session.commit()

        run = session.get(PipelineRun, run_id)
        assert run.status == "ABORTED"
        assert fetched_from == []  # no source was even fetched
