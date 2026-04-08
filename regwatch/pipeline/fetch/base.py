"""Source protocol and registry for pipeline fetch plugins."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from regwatch.domain.types import RawDocument


@runtime_checkable
class Source(Protocol):
    name: str

    def fetch(self, since: datetime) -> Iterator[RawDocument]: ...


REGISTRY: dict[str, type] = {}


def register_source(cls: type) -> type:
    """Decorator / function that registers a Source subclass by its `name`."""
    name = getattr(cls, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError(f"Source {cls!r} must define a non-empty `name` class attribute")
    REGISTRY[name] = cls
    return cls
