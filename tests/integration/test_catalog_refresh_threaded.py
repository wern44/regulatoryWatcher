"""Integration tests: POST /catalog/refresh runs in a background thread and
drives analysis_progress so the user sees it in the status bar with abort.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from regwatch.db.models import (
    LifecycleStage,
    Regulation,
    RegulationType,
)


def _build_config(tmp_path: Path) -> Path:
    shutil.copy("config.example.yaml", tmp_path / "config.yaml")
    cfg_path = tmp_path / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["paths"]["db_file"] = str(tmp_path / "app.db")
    data["paths"]["pdf_archive"] = str(tmp_path / "pdfs")
    data["paths"]["uploads_dir"] = str(tmp_path / "uploads")
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "uploads").mkdir()
    cfg_path.write_text(yaml.safe_dump(data))
    return cfg_path


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    cfg_path = _build_config(tmp_path)
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))
    import importlib

    import regwatch.main as main_module

    importlib.reload(main_module)
    app = main_module.create_app()
    app.state.llm_client.chat_model = "test-model"
    return TestClient(app)


def _seed_regulation(client: TestClient, ref: str) -> None:
    with client.app.state.session_factory() as s:
        s.add(Regulation(
            reference_number=ref,
            type=RegulationType.CSSF_CIRCULAR,
            title=f"Test {ref}",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            url=f"https://example.com/{ref}",
            source_of_truth="SEED",
        ))
        s.commit()


def _wait_for(predicate, timeout: float = 5.0, poll: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


def test_catalog_refresh_returns_immediately_and_runs_in_background(
    tmp_path: Path, monkeypatch
) -> None:
    """The endpoint must not block on LLM calls — it must spawn a worker
    and redirect, with progress visible on app.state.analysis_progress.
    """
    client = _client(tmp_path, monkeypatch)
    _seed_regulation(client, "CSSF 18/698")

    # Replace the LLM mock with one that blocks until we release it, so we can
    # assert the request thread does NOT wait for the LLM to respond.
    import threading
    release = threading.Event()
    call_count = {"n": 0}

    def slow_chat(*a, system: str = "", **kw):
        call_count["n"] += 1
        release.wait(timeout=2.0)
        # Distinguish classify vs. discover by inspecting the system prompt.
        if "missing" in (system or kw.get("system", "")).lower():
            return "[]"
        return json.dumps({
            "is_ict": False,
            "dora_pillar": None,
            "applicable_entity_types": ["ALL"],
            "is_superseded": False,
            "superseded_by": None,
            "confidence": 0.9,
        })

    client.app.state.llm_client.chat = slow_chat

    t0 = time.time()
    resp = client.post("/catalog/refresh", follow_redirects=False)
    elapsed = time.time() - t0

    # Endpoint returned without waiting for the LLM (which is blocked on `release`).
    assert resp.status_code == 303
    assert elapsed < 1.0, f"endpoint blocked for {elapsed:.2f}s"

    # The worker thread should have started and tagged analysis_progress.
    assert _wait_for(
        lambda: client.app.state.analysis_progress.status == "running",
        timeout=3.0,
    )

    release.set()  # let the worker finish

    assert _wait_for(
        lambda: client.app.state.analysis_progress.status != "running",
        timeout=5.0,
    )


def test_catalog_refresh_abort_stops_worker_quickly(
    tmp_path: Path, monkeypatch
) -> None:
    """Posting /analysis/abort while /catalog/refresh runs should cause the
    DiscoveryService loop to exit cooperatively without making more LLM calls.
    """
    client = _client(tmp_path, monkeypatch)
    for ref in ("CSSF 18/698", "CSSF 20/750", "CSSF 22/806"):
        _seed_regulation(client, ref)

    import threading
    release = threading.Event()
    call_count = {"n": 0}

    def slow_chat(*a, **kw):
        call_count["n"] += 1
        # Block on the first call so we have a window to abort.
        if call_count["n"] == 1:
            release.wait(timeout=5.0)
        return json.dumps({
            "is_ict": False,
            "dora_pillar": None,
            "applicable_entity_types": ["ALL"],
            "is_superseded": False,
            "superseded_by": None,
            "confidence": 0.9,
        })

    client.app.state.llm_client.chat = slow_chat

    client.post("/catalog/refresh", follow_redirects=False)

    # Wait for the worker to enter the LLM call.
    assert _wait_for(lambda: call_count["n"] >= 1, timeout=3.0)

    abort_resp = client.post("/analysis/abort")
    assert abort_resp.status_code in (200, 303)
    assert client.app.state.analysis_progress.is_cancel_requested is True

    release.set()  # release the in-flight LLM call so the worker can observe cancel

    # Worker should exit before making the LLM call for the remaining 2 regulations
    # (and before discover_missing fires).
    assert _wait_for(
        lambda: client.app.state.analysis_progress.status != "running",
        timeout=5.0,
    )
    assert call_count["n"] == 1, (
        f"expected exactly 1 LLM call before abort took effect, got {call_count['n']}"
    )
