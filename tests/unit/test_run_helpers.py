from unittest.mock import MagicMock, patch

from regwatch.pipeline.progress import PipelineProgress
from regwatch.pipeline.run_helpers import run_pipeline_background


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
