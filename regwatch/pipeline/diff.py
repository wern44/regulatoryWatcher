"""Compute unified diffs between two document version texts."""
from __future__ import annotations

import difflib


def compute_diff(old: str, new: str, *, context_lines: int = 3) -> str | None:
    """Return a unified diff string, or None if the texts are identical."""
    if old == new:
        return None
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile="previous",
        tofile="current",
        n=context_lines,
    )
    return "".join(lines) or None
