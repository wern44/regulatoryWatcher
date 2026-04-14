"""Thread-safe analysis-run progress snapshot for the web UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock


@dataclass
class AnalysisProgress:
    status: str = "idle"
    run_id: int | None = None
    total: int = 0
    done: int = 0
    current_label: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def start(self, run_id: int, total: int) -> None:
        with self._lock:
            self.status = "running"
            self.run_id = run_id
            self.total = total
            self.done = 0
            self.current_label = None
            self.started_at = datetime.now(UTC)
            self.finished_at = None
            self.error = None

    def tick(self, done: int, total: int, label: str) -> None:
        with self._lock:
            self.done = done
            self.total = total
            self.current_label = label

    def finish(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self.status = status
            self.finished_at = datetime.now(UTC)
            self.error = error
