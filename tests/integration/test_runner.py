from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, PipelineRun, UpdateEvent
from regwatch.domain.types import RawDocument
from regwatch.pipeline.runner import PipelineRunner


class _FakeSource:
    name = "fake_success"

    def __init__(self, docs: list[RawDocument]) -> None:
        self._docs = docs

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        yield from self._docs


class _FailingSource:
    name = "fake_failing"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        raise RuntimeError("boom")


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _raw(title: str, url: str, text: str) -> RawDocument:
    now = datetime.now(timezone.utc)
    return RawDocument(
        source="fake_success",
        source_url=url,
        title=title,
        published_at=now,
        raw_payload={"text": text},
        fetched_at=now,
    )


def test_runner_success_path(tmp_path: Path) -> None:
    session = _session(tmp_path)
    source = _FakeSource(
        [
            _raw("Doc A", "https://example.com/a", "Some content A"),
            _raw("Doc B", "https://example.com/b", "Some content B"),
        ]
    )

    runner = PipelineRunner(
        session,
        sources=[source],
        extract=lambda raw: _stub_extract(raw),
        match=lambda extracted: _stub_match(extracted),
    )
    run_id = runner.run_once()
    session.commit()

    pr = session.get(PipelineRun, run_id)
    assert pr is not None
    assert pr.status == "COMPLETED"
    assert pr.events_created == 2
    assert pr.sources_attempted == ["fake_success"]
    assert pr.sources_failed == []

    events = session.query(UpdateEvent).all()
    assert len(events) == 2


def test_runner_failing_source_does_not_block_others(tmp_path: Path) -> None:
    session = _session(tmp_path)
    good = _FakeSource([_raw("Doc A", "https://example.com/a", "content")])
    bad = _FailingSource()

    runner = PipelineRunner(
        session,
        sources=[bad, good],
        extract=lambda raw: _stub_extract(raw),
        match=lambda extracted: _stub_match(extracted),
    )
    run_id = runner.run_once()
    session.commit()

    pr = session.get(PipelineRun, run_id)
    assert pr is not None
    assert pr.status == "COMPLETED_WITH_ERRORS"
    assert "fake_failing" in pr.sources_failed
    assert "fake_success" in pr.sources_attempted
    assert pr.events_created == 1


def _stub_extract(raw: RawDocument):
    from regwatch.domain.types import ExtractedDocument

    return ExtractedDocument(
        raw=raw,
        html_text=raw.raw_payload.get("text"),
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )


def _stub_match(extracted):
    from regwatch.domain.types import MatchedDocument

    return MatchedDocument(
        extracted=extracted,
        references=[],
        lifecycle_stage="IN_FORCE",
        is_ict=False,
        severity="INFORMATIONAL",
    )
