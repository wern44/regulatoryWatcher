"""Unit tests for CssfDiscoveryService (non-DB assertions)."""


def test_enrich_stubs_method_removed():
    """enrich_stubs is gone — no backward-compat alias."""
    from regwatch.services.cssf_discovery import CssfDiscoveryService

    assert not hasattr(CssfDiscoveryService, "enrich_stubs"), (
        "enrich_stubs should be removed; filter-matrix crawl promotes stubs."
    )
