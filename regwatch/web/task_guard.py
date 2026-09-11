"""One long-running task at a time.

SQLite allows a single writer. The pipeline, CSSF discovery, catalog / ICT
refresh and analysis each write for minutes to hours, so two of them at once
end in "database is locked" -- and the refresh and analysis share one
progress object, so they would also overwrite each other's status. Every
start point (web routes and scheduled jobs) claims the slot through
``try_start``; a refused web request shows the running task's name in the
global status bar via a one-shot cookie.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse

BUSY_COOKIE = "task_busy"

_start_lock = threading.Lock()


def running_task(state: Any) -> str | None:
    """Name of the long-running task in progress (e.g. "ICT refresh"), or None."""
    pipeline = getattr(state, "pipeline_progress", None)
    if pipeline is not None and pipeline.snapshot()["status"] == "running":
        return "A pipeline run"
    discovery = getattr(state, "cssf_discovery_progress", None)
    if discovery is not None and discovery.status == "running":
        return "A CSSF discovery run"
    analysis = getattr(state, "analysis_progress", None)
    if analysis is not None and analysis.status == "running":
        return str(analysis.task)
    return None


def try_start(state: Any, mark_running: Callable[[], None]) -> str | None:
    """Claim the task slot: return None and call ``mark_running``, or return
    the name of the task already running.

    ``mark_running`` must flip a progress object to "running". It runs under
    the lock, before the caller spawns its worker, so two clicks in quick
    succession can't both get through.
    """
    with _start_lock:
        busy = running_task(state)
        if busy is None:
            mark_running()
        return busy


def busy_message(busy: str) -> str:
    return f"{busy} is already running. Wait for it to finish, or abort it, and try again."


def refuse(request: Request, fallback_url: str, busy: str) -> RedirectResponse:
    """Redirect back to the page the user came from, flagging the refusal."""
    url = fallback_url
    referer = urlsplit(request.headers.get("referer", ""))
    if referer.path and referer.netloc == request.url.netloc:
        url = referer.path + (f"?{referer.query}" if referer.query else "")
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(
        BUSY_COOKIE, quote(busy_message(busy)), max_age=60, httponly=True, samesite="lax"
    )
    return resp


def refused_message(request: Request) -> str:
    return unquote(request.cookies.get(BUSY_COOKIE, ""))
