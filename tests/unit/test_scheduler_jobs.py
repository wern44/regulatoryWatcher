from unittest.mock import MagicMock

import pytest

from regwatch.config import AppConfig, SourceConfig
from regwatch.scheduler.jobs import (
    SOURCE_TO_JOB,
    assert_sources_have_jobs,
    build_scheduler,
)


def _minimal_config(enabled_sources: dict[str, SourceConfig]) -> AppConfig:
    return AppConfig.model_validate(
        {
            "entity": {
                "lei": "L",
                "legal_name": "X",
                "authorizations": [{"type": "AIFM", "cssf_entity_id": "1"}],
            },
            "sources": {k: v.model_dump() for k, v in enabled_sources.items()},
            "ollama": {
                "base_url": "http://x",
                "chat_model": "x",
                "embedding_model": "x",
                "embedding_dim": 1,
            },
            "rag": {
                "chunk_size_tokens": 1,
                "chunk_overlap_tokens": 0,
                "retrieval_k": 1,
                "rerank_k": 1,
                "enable_rerank": False,
            },
            "paths": {"db_file": "x", "pdf_archive": "x", "uploads_dir": "x"},
            "ui": {"language": "en", "timezone": "UTC", "host": "x", "port": 1},
        }
    )


def test_all_registered_sources_have_job_mapping() -> None:
    expected = {
        "cssf_rss",
        "cssf_consultation",
        "eur_lex_adopted",
        "eur_lex_proposal",
        "legilux_sparql",
        "legilux_parliamentary",
        "esma_rss",
        "eba_rss",
        "ec_fisma_rss",
    }
    assert expected.issubset(SOURCE_TO_JOB.keys())


def test_assert_raises_on_unmapped_enabled_source() -> None:
    cfg = _minimal_config(
        {"unknown_source": SourceConfig(enabled=True, interval_hours=6)}
    )
    with pytest.raises(ValueError, match="unknown_source"):
        assert_sources_have_jobs(cfg)


def test_disabled_source_is_not_required_to_have_job() -> None:
    cfg = _minimal_config(
        {"unknown_source": SourceConfig(enabled=False, interval_hours=6)}
    )
    assert_sources_have_jobs(cfg)  # must not raise


def test_build_scheduler_returns_running_scheduler() -> None:
    cfg = _minimal_config(
        {
            "cssf_rss": SourceConfig(
                enabled=True, interval_hours=6, keywords=["aif"]
            ),
        }
    )
    fake_run = MagicMock()
    scheduler = build_scheduler(cfg, run_pipeline_for=fake_run, start=False)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "run_pipeline_cssf" in job_ids
