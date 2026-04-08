from collections.abc import Iterator
from datetime import datetime

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import REGISTRY, register_source


def test_register_and_lookup() -> None:
    class FakeSource:
        name = "fake_unit_test_source"

        def fetch(self, since: datetime) -> Iterator[RawDocument]:  # pragma: no cover
            return iter([])

    register_source(FakeSource)
    assert "fake_unit_test_source" in REGISTRY
    assert REGISTRY["fake_unit_test_source"] is FakeSource


def test_register_rejects_missing_name() -> None:
    class NoName:
        def fetch(self, since: datetime) -> Iterator[RawDocument]:  # pragma: no cover
            return iter([])

    import pytest

    with pytest.raises(ValueError, match="must define a non-empty `name`"):
        register_source(NoName)
