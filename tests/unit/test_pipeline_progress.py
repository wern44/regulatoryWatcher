from regwatch.pipeline.progress import PipelineProgress


def test_initial_state_is_idle() -> None:
    p = PipelineProgress()
    snap = p.snapshot()
    assert snap["status"] == "idle"
    assert snap["total_sources"] == 0
    assert snap["docs_seen"] == 0
    assert snap["error"] is None
    assert snap["run_id"] is None


def test_full_lifecycle_of_a_successful_run() -> None:
    p = PipelineProgress()

    p.reset_for_run(total_sources=2)
    s = p.snapshot()
    assert s["status"] == "running"
    assert s["total_sources"] == 2
    assert s["sources_failed"] == []

    p.begin_source("cssf_rss", 1)
    p.begin_document("Circular CSSF 25/901")
    p.set_phase("MATCH")
    p.set_phase("PERSIST")
    p.add_persist_result(events=1, versions=1)

    p.begin_source("eba_rss", 2)
    p.begin_document("EBA news 1")
    p.add_persist_result(events=1, versions=0)

    s = p.snapshot()
    assert s["docs_seen"] == 2
    assert s["events_created"] == 2
    assert s["versions_created"] == 1
    assert s["source_index"] == 2

    p.finish(run_id=42)
    s = p.snapshot()
    assert s["status"] == "completed"
    assert s["run_id"] == 42
    assert s["finished_at"] is not None
    assert s["current_phase"] == "DONE"
    assert "Pipeline run #42" in s["message"]
    assert s["elapsed_seconds"] >= 0


def test_failed_source_is_recorded_but_run_continues() -> None:
    p = PipelineProgress()
    p.reset_for_run(total_sources=2)

    p.begin_source("legilux_sparql", 1)
    p.fail_source("legilux_sparql")

    p.begin_source("cssf_rss", 2)
    p.add_persist_result(events=3, versions=2)
    p.finish(run_id=7)

    s = p.snapshot()
    assert s["status"] == "completed"
    assert s["sources_failed"] == ["legilux_sparql"]
    assert s["events_created"] == 3


def test_finish_with_error_marks_failed() -> None:
    p = PipelineProgress()
    p.reset_for_run(total_sources=1)
    p.finish(run_id=None, error="Boom")

    s = p.snapshot()
    assert s["status"] == "failed"
    assert s["error"] == "Boom"
    assert s["run_id"] is None
    assert "Boom" in s["message"]


def test_snapshot_does_not_alias_internal_lists() -> None:
    p = PipelineProgress()
    p.reset_for_run(total_sources=1)
    p.fail_source("x")
    s = p.snapshot()
    s["sources_failed"].append("MUTATED")
    assert p.snapshot()["sources_failed"] == ["x"]
