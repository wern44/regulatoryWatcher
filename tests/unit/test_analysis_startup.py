from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.analysis.startup import sweep_stuck_runs
from regwatch.db.models import AnalysisRun, AnalysisRunStatus, Base


def _mk(s: Session, *, status: AnalysisRunStatus, started_minutes_ago: int) -> int:
    run = AnalysisRun(
        status=status,
        queued_version_ids=[],
        started_at=datetime.now(UTC) - timedelta(minutes=started_minutes_ago),
        llm_model="t",
        triggered_by="USER_UI",
    )
    s.add(run); s.flush()
    return run.run_id


def test_sweep_marks_old_running_as_failed():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        stale = _mk(s, status=AnalysisRunStatus.RUNNING, started_minutes_ago=30)
        fresh = _mk(s, status=AnalysisRunStatus.RUNNING, started_minutes_ago=2)
        finished = _mk(s, status=AnalysisRunStatus.SUCCESS, started_minutes_ago=60)
        s.commit()
        count = sweep_stuck_runs(s, threshold_minutes=10)
        s.commit()
        assert count == 1
        assert s.get(AnalysisRun, stale).status is AnalysisRunStatus.FAILED
        assert s.get(AnalysisRun, stale).error_summary == "interrupted before completion"
        assert s.get(AnalysisRun, stale).finished_at is not None
        assert s.get(AnalysisRun, fresh).status is AnalysisRunStatus.RUNNING
        assert s.get(AnalysisRun, finished).status is AnalysisRunStatus.SUCCESS


def test_sweep_returns_zero_when_nothing_stale():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        _mk(s, status=AnalysisRunStatus.SUCCESS, started_minutes_ago=60)
        s.commit()
        assert sweep_stuck_runs(s, threshold_minutes=10) == 0


def test_sweep_handles_null_started_at():
    """If started_at is somehow NULL, don't blow up — treat as stale."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        run = AnalysisRun(
            status=AnalysisRunStatus.RUNNING, queued_version_ids=[],
            started_at=None, llm_model="t", triggered_by="USER_UI",
        )
        s.add(run); s.commit()
        count = sweep_stuck_runs(s, threshold_minutes=10)
        s.commit()
        assert count == 1
        assert s.get(AnalysisRun, run.run_id).status is AnalysisRunStatus.FAILED
