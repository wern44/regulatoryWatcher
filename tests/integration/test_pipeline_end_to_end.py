from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationAlias,
    RegulationType,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.domain.types import RawDocument
from regwatch.pipeline.pipeline_factory import build_runner


class _FakeSource:
    name = "fake_end_to_end"

    def __init__(self, docs: list[RawDocument]) -> None:
        self._docs = docs

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        yield from self._docs


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_end_to_end_rule_match_without_ollama(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 18/698",
        title="IFM",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    reg.aliases.append(RegulationAlias(pattern=r"CSSF[\s\-]?18[/\-]698", kind="REGEX"))
    session.add(reg)
    session.commit()

    now = datetime.now(timezone.utc)
    raw = RawDocument(
        source="fake_end_to_end",
        source_url="https://example.com/x",
        title="New note referencing CSSF 18/698",
        published_at=now,
        raw_payload={"html_text": "This note refers to CSSF 18/698 and nothing else."},
        fetched_at=now,
    )

    runner = build_runner(
        session,
        sources=[_FakeSource([raw])],
        archive_root=tmp_path / "pdfs",
        ollama_enabled=False,
    )
    runner.run_once()
    session.commit()

    events = session.query(UpdateEvent).all()
    assert len(events) == 1
    links = session.query(UpdateEventRegulationLink).all()
    assert len(links) == 1
    assert links[0].regulation_id == reg.regulation_id
    assert links[0].match_method == "REGEX_ALIAS"
