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
