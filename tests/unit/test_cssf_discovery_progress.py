from regwatch.discovery.progress import CssfDiscoveryProgress


def test_initial_state_is_idle():
    p = CssfDiscoveryProgress()
    assert p.status == "idle"
    assert p.run_id is None
    assert p.total_scraped == 0
    assert p.started_at is None


def test_start_sets_run_id_and_running():
    p = CssfDiscoveryProgress()
    p.start(run_id=42)
    assert p.status == "running"
    assert p.run_id == 42
    assert p.started_at is not None
    assert p.finished_at is None


def test_tick_updates_counts_and_current_fields():
    p = CssfDiscoveryProgress()
    p.start(run_id=1)
    p.tick(total_scraped=5, entity_type="AIFM", reference="CSSF 22/806")
    assert p.total_scraped == 5
    assert p.current_entity_type == "AIFM"
    assert p.current_reference == "CSSF 22/806"
    # Partial tick preserves non-supplied fields
    p.tick(total_scraped=6)
    assert p.total_scraped == 6
    assert p.current_entity_type == "AIFM"


def test_finish_sets_terminal_state():
    p = CssfDiscoveryProgress()
    p.start(run_id=1)
    p.finish("SUCCESS")
    assert p.status == "SUCCESS"
    assert p.finished_at is not None
    assert p.error is None


def test_finish_records_error():
    p = CssfDiscoveryProgress()
    p.start(run_id=1)
    p.finish("FAILED", error="timeout")
    assert p.status == "FAILED"
    assert p.error == "timeout"
