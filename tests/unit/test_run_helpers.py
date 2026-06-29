import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import Base, Setting
from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.run_helpers import run_pipeline_background
from regwatch.services.settings import SettingsService


def test_reports_source_build_error_via_progress():
    progress = PipelineProgress()
    with patch(
        "regwatch.pipeline.run_helpers.build_enabled_sources",
        side_effect=RuntimeError("bad source"),
    ):
        run_pipeline_background(
            session_factory=MagicMock(),
            config=MagicMock(),
            llm_client=MagicMock(),
            progress=progress,
        )
    snap = progress.snapshot()
    assert snap["status"] == "failed"
    assert "bad source" in snap["error"]


def test_passes_source_names_to_build_enabled_sources():
    progress = PipelineProgress()
    mock_session = MagicMock()
    mock_sf = MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=mock_session),
        __exit__=MagicMock(return_value=False),
    ))

    with patch(
        "regwatch.pipeline.run_helpers.build_enabled_sources",
        return_value=[],
    ) as mock_build, patch(
        "regwatch.pipeline.run_helpers.build_runner",
    ) as mock_runner:
        mock_runner.return_value.run_once.return_value = 1
        run_pipeline_background(
            session_factory=mock_sf,
            config=MagicMock(),
            llm_client=MagicMock(),
            progress=progress,
            source_names=["cssf_rss", "cssf_consultation"],
        )
    mock_build.assert_called_once()
    call_kwargs = mock_build.call_args
    assert call_kwargs.kwargs.get("only") == ["cssf_rss", "cssf_consultation"]


def test_aborted_run_calls_finish_with_aborted_true():
    progress = PipelineProgress()
    mock_session = MagicMock()
    mock_sf = MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=mock_session),
        __exit__=MagicMock(return_value=False),
    ))

    # Pretend a cancel was requested before the runner returned.
    progress.request_cancel()

    with patch(
        "regwatch.pipeline.run_helpers.build_enabled_sources",
        return_value=[],
    ), patch(
        "regwatch.pipeline.run_helpers.build_runner",
    ) as mock_runner:
        mock_runner.return_value.run_once.return_value = 99
        run_pipeline_background(
            session_factory=mock_sf,
            config=MagicMock(),
            llm_client=MagicMock(),
            progress=progress,
        )

    assert progress.snapshot()["status"] == "aborted"
    assert progress.snapshot()["run_id"] == 99


def test_max_runtime_aborts_a_slow_run():
    """A pipeline that runs past its max-runtime setting is aborted cooperatively."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Setting.__table__])
    with Session(engine) as s:
        SettingsService(s).set("pipeline_max_runtime_seconds", "1")
        s.commit()

    def session_factory() -> Session:
        return Session(engine)

    config = SimpleNamespace(
        analysis=SimpleNamespace(max_pipeline_runtime_seconds=0),
        paths=SimpleNamespace(pdf_archive="/tmp/unused"),
    )
    progress = PipelineProgress()
    progress.reset_for_run(total_sources=1)

    def fake_run_once(*, progress):
        # Cooperative worker: stop as soon as a cancel is requested, else
        # keep going well past the 1s deadline.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if progress.is_cancel_requested:
                return 7
            time.sleep(0.02)
        return 7

    with patch(
        "regwatch.pipeline.run_helpers.build_enabled_sources",
        return_value=[object()],
    ), patch(
        "regwatch.pipeline.run_helpers.build_runner",
    ) as mock_runner:
        mock_runner.return_value.run_once.side_effect = fake_run_once
        run_pipeline_background(
            session_factory=session_factory,
            config=config,
            llm_client=MagicMock(),
            progress=progress,
        )

    snap = progress.snapshot()
    assert snap["status"] == "aborted"
    assert "maximum runtime" in snap["message"]
