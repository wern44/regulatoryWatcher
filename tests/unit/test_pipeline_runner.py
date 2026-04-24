from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import Base, PipelineRun
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
