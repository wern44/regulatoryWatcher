"""Unit tests for max-runtime resolution and the cooperative watchdog."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Event, RLock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.config import AnalysisConfig
from regwatch.db.models import Base, Setting
from regwatch.services.runtime_limits import (
    get_max_runtime_seconds,
    runtime_watchdog,
)
from regwatch.services.settings import SettingsService


def _session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Setting.__table__])

    def factory() -> Session:
        return Session(engine)

    return factory


class _Config:
    def __init__(self, **kwargs: int) -> None:
        self.analysis = AnalysisConfig(**kwargs)


def test_resolve_uses_config_default_when_no_setting() -> None:
    factory = _session_factory()
    cfg = _Config(max_pipeline_runtime_seconds=300)
    with factory() as s:
        assert get_max_runtime_seconds(s, cfg, "pipeline") == 300
        assert get_max_runtime_seconds(s, cfg, "analysis") == 0


def test_resolve_setting_overrides_config() -> None:
    factory = _session_factory()
    cfg = _Config(max_pipeline_runtime_seconds=300)
    with factory() as s:
        SettingsService(s).set("pipeline_max_runtime_seconds", "45")
        s.commit()
    with factory() as s:
        assert get_max_runtime_seconds(s, cfg, "pipeline") == 45


def test_resolve_malformed_setting_falls_back_to_default() -> None:
    factory = _session_factory()
    cfg = _Config(max_analysis_runtime_seconds=120)
    with factory() as s:
        SettingsService(s).set("analysis_max_runtime_seconds", "not-a-number")
        s.commit()
    with factory() as s:
        assert get_max_runtime_seconds(s, cfg, "analysis") == 120


def test_resolve_clamps_negative_to_zero() -> None:
    factory = _session_factory()
    cfg = _Config()
    with factory() as s:
        SettingsService(s).set("pipeline_max_runtime_seconds", "-10")
        s.commit()
    with factory() as s:
        assert get_max_runtime_seconds(s, cfg, "pipeline") == 0


@dataclass
class _FakeProgress:
    status: str = "running"
    _lock: RLock = field(default_factory=RLock, repr=False)
    _cancel: Event = field(default_factory=Event, repr=False)

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def request_cancel(self) -> None:
        self._cancel.set()


def test_watchdog_trips_after_deadline() -> None:
    progress = _FakeProgress()
    with runtime_watchdog(progress, 1, label="Test") as watch:
        # Simulate a worker that runs past the 1s deadline.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not progress.is_cancel_requested:
            time.sleep(0.02)
    assert watch.timed_out is True
    assert progress.is_cancel_requested is True


def test_watchdog_noop_when_finishes_early() -> None:
    progress = _FakeProgress()
    with runtime_watchdog(progress, 5, label="Test") as watch:
        pass  # finishes immediately
    time.sleep(0.05)
    assert watch.timed_out is False
    assert progress.is_cancel_requested is False


def test_watchdog_disabled_when_zero() -> None:
    progress = _FakeProgress()
    with runtime_watchdog(progress, 0, label="Test") as watch:
        time.sleep(0.05)
    assert watch.timed_out is False
    assert progress.is_cancel_requested is False
