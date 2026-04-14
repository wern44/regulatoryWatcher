"""Thread-safe CSSF discovery progress snapshot for the web UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock


@dataclass
class CssfDiscoveryProgress:
    status: str = "idle"
    run_id: int | None = None
    total_scraped: int = 0
    current_entity_type: str | None = None
    current_reference: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def start(self, run_id: int) -> None:
        with self._lock:
            self.status = "running"
            self.run_id = run_id
            self.total_scraped = 0
            self.current_entity_type = None
            self.current_reference = None
            self.started_at = datetime.now(UTC)
            self.finished_at = None
            self.error = None

    def tick(
        self, *, total_scraped: int, entity_type: str | None = None, reference: str | None = None
    ) -> None:
        with self._lock:
            self.total_scraped = total_scraped
            if entity_type is not None:
                self.current_entity_type = entity_type
            if reference is not None:
                self.current_reference = reference

    def finish(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self.status = status
            self.finished_at = datetime.now(UTC)
            self.error = error
