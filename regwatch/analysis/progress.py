"""Thread-safe analysis-run progress snapshot for the web UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, RLock


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
    _cancel_event: Event = field(default_factory=Event, repr=False, compare=False)

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
            self._cancel_event.clear()

    def tick(self, done: int, total: int, label: str) -> None:
        with self._lock:
            self.done = done
            self.total = total
            self.current_label = label

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel_event.set()
            if self.status == "running":
                self.current_label = (
                    "Cancellation requested — finishing current item…"
                )

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def finish(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self.status = status
            self.finished_at = datetime.now(UTC)
            self.error = error
