"""Thread-safe in-memory pipeline progress tracker.

A single instance is created per app on startup and lives on
`app.state.pipeline_progress`. The pipeline runner writes to it from a
background worker thread; the web UI reads snapshots of it via an HTMX
polling endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any


@dataclass
class PipelineProgress:
    """Snapshot of an in-flight pipeline run.

    All mutation goes through `update()`, which holds an internal lock so
    the worker thread and the polling reader never see a torn state.
    """

    status: str = "idle"  # idle | running | completed | failed
    started_at: datetime | None = None
    finished_at: datetime | None = None

    total_sources: int = 0
    source_index: int = 0  # 1-based, 0 means "not yet started"
    current_source: str | None = None
    current_phase: str | None = None  # FETCH | EXTRACT | MATCH | PERSIST | DONE

    docs_seen: int = 0  # documents pulled from sources so far
    current_doc_title: str | None = None

    events_created: int = 0
    versions_created: int = 0
    sources_failed: list[str] = field(default_factory=list)

    message: str = ""
    error: str | None = None
    run_id: int | None = None

    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def reset_for_run(self, total_sources: int) -> None:
        with self._lock:
            self.status = "running"
            self.started_at = datetime.now(UTC)
            self.finished_at = None
            self.total_sources = total_sources
            self.source_index = 0
            self.current_source = None
            self.current_phase = None
            self.docs_seen = 0
            self.current_doc_title = None
            self.events_created = 0
            self.versions_created = 0
            self.sources_failed = []
            self.message = "Starting pipeline..."
            self.error = None
            self.run_id = None

    def begin_source(self, name: str, index: int) -> None:
        with self._lock:
            self.current_source = name
            self.source_index = index
            self.current_phase = "FETCH"
            self.message = f"Fetching from {name} ({index}/{self.total_sources})"

    def fail_source(self, name: str) -> None:
        with self._lock:
            if name not in self.sources_failed:
                self.sources_failed.append(name)
            self.message = f"Source {name} failed; continuing with the next one"

    def begin_document(self, title: str) -> None:
        with self._lock:
            self.docs_seen += 1
            self.current_doc_title = title
            self.current_phase = "EXTRACT"
            self.message = f"Processing doc {self.docs_seen}: {title[:80]}"

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.current_phase = phase

    def add_persist_result(self, events: int, versions: int) -> None:
        with self._lock:
            self.events_created += events
            self.versions_created += versions

    def finish(self, *, run_id: int | None, error: str | None = None) -> None:
        with self._lock:
            self.finished_at = datetime.now(UTC)
            self.run_id = run_id
            self.current_phase = "DONE"
            self.current_source = None
            self.current_doc_title = None
            if error:
                self.status = "failed"
                self.error = error
                self.message = f"Pipeline failed: {error}"
            else:
                self.status = "completed"
                self.message = (
                    f"Pipeline run #{run_id} completed — "
                    f"{self.events_created} new event(s)"
                )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "total_sources": self.total_sources,
                "source_index": self.source_index,
                "current_source": self.current_source,
                "current_phase": self.current_phase,
                "docs_seen": self.docs_seen,
                "current_doc_title": self.current_doc_title,
                "events_created": self.events_created,
                "versions_created": self.versions_created,
                "sources_failed": list(self.sources_failed),
                "message": self.message,
                "error": self.error,
                "run_id": self.run_id,
                "elapsed_seconds": (
                    int(
                        (
                            (self.finished_at or datetime.now(UTC))
                            - self.started_at
                        ).total_seconds()
                    )
                    if self.started_at
                    else 0
                ),
            }
