"""Tests for AnalysisProgress cancellation hook."""
from __future__ import annotations

from regwatch.analysis.progress import AnalysisProgress


def test_request_cancel_sets_flag() -> None:
    progress = AnalysisProgress()
    progress.start(run_id=1, total=10)

    assert progress.is_cancel_requested is False

    progress.request_cancel()

    assert progress.is_cancel_requested is True


def test_start_clears_previous_cancel() -> None:
    progress = AnalysisProgress()
    progress.start(run_id=1, total=10)
    progress.request_cancel()
    assert progress.is_cancel_requested is True

    progress.start(run_id=2, total=5)

    assert progress.is_cancel_requested is False


def test_finish_aborted_sets_status_aborted() -> None:
    progress = AnalysisProgress()
    progress.start(run_id=1, total=10)
    progress.request_cancel()

    progress.finish("ABORTED")

    assert progress.status == "ABORTED"
