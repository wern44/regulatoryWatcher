"""Helpers to import and instantiate fetch sources from AppConfig.

Shared by the CLI (`regwatch run-pipeline`) and the web UI "Run pipeline"
button so both paths construct sources the same way.
"""
from __future__ import annotations

from typing import Any

from regwatch.config import AppConfig, SourceConfig
from regwatch.pipeline.fetch.base import REGISTRY

# Logical groupings of sources for selective pipeline runs.
SOURCE_GROUPS: dict[str, list[str]] = {
    "cssf": ["cssf_rss", "cssf_consultation"],
    "eu_legislation": ["eur_lex_adopted", "eur_lex_proposal"],
    "luxembourg": ["legilux_sparql", "legilux_parliamentary"],
    "eu_agencies": ["esma_rss", "eba_rss", "ec_fisma_rss"],
}

SOURCE_GROUP_LABELS: dict[str, str] = {
    "cssf": "CSSF",
    "eu_legislation": "EU Legislation",
    "luxembourg": "Luxembourg",
    "eu_agencies": "EU Agencies",
}


def import_all_sources() -> None:
    """Side-effect imports that register every built-in Source in REGISTRY."""
    import regwatch.pipeline.fetch.cssf_consultation  # noqa: F401, PLC0415
    import regwatch.pipeline.fetch.cssf_rss  # noqa: F401, PLC0415
    import regwatch.pipeline.fetch.eba_rss  # noqa: F401, PLC0415
    import regwatch.pipeline.fetch.ec_fisma_rss  # noqa: F401, PLC0415
    import regwatch.pipeline.fetch.esma_rss  # noqa: F401, PLC0415
    import regwatch.pipeline.fetch.eur_lex_adopted  # noqa: F401, PLC0415
    import regwatch.pipeline.fetch.eur_lex_proposal  # noqa: F401, PLC0415
    import regwatch.pipeline.fetch.legilux_parliamentary  # noqa: F401, PLC0415
    import regwatch.pipeline.fetch.legilux_sparql  # noqa: F401, PLC0415


def instantiate_source(name: str, source_cfg: SourceConfig) -> Any:
    """Instantiate a registered source class with the arguments it expects."""
    cls = REGISTRY[name]
    if name == "cssf_rss":
        return cls(keywords=source_cfg.keywords)
    if name == "eur_lex_adopted":
        return cls(celex_prefixes=source_cfg.celex_prefixes)
    if name == "ec_fisma_rss":
        return cls(
            item_types=source_cfg.item_types,
            topic_ids=source_cfg.topic_ids,
        )
    return cls()


def build_enabled_sources(
    config: AppConfig, *, only: str | list[str] | None = None
) -> list[Any]:
    """Instantiate every enabled source in the config.

    If `only` is a string, restrict to that single source name.
    If `only` is a list, restrict to those source names.
    """
    import_all_sources()
    only_set: set[str] | None = None
    if isinstance(only, str):
        only_set = {only}
    elif isinstance(only, list):
        only_set = set(only)

    instances: list[Any] = []
    for name, source_cfg in config.sources.items():
        if not source_cfg.enabled:
            continue
        if only_set is not None and name not in only_set:
            continue
        instances.append(instantiate_source(name, source_cfg))
    return instances
