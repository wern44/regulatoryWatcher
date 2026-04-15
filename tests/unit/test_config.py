from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from regwatch.config import (
    AppConfig,
    CssfDiscoveryConfig,
    PublicationTypeConfig,
    load_config,
)


def test_load_config_parses_example_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "entity": {
                    "lei": "529900FSORICM1ERBP05",
                    "legal_name": "Union Investment Luxembourg S.A.",
                    "authorizations": [
                        {"type": "AIFM", "cssf_entity_id": "7073800"},
                        {"type": "CHAPTER15_MANCO", "cssf_entity_id": "6918042"},
                    ],
                },
                "sources": {
                    "cssf_rss": {
                        "enabled": True,
                        "interval_hours": 6,
                        "keywords": ["aif", "ucits"],
                    }
                },
                "llm": {
                    "base_url": "http://localhost:11434",
                    "chat_model": "llama3.1:8b",
                    "embedding_model": "nomic-embed-text",
                    "embedding_dim": 768,
                },
                "rag": {
                    "chunk_size_tokens": 500,
                    "chunk_overlap_tokens": 50,
                    "retrieval_k": 20,
                    "rerank_k": 10,
                    "enable_rerank": False,
                },
                "paths": {
                    "db_file": "./data/app.db",
                    "pdf_archive": "./data/pdfs",
                    "uploads_dir": "./data/uploads",
                },
                "ui": {
                    "language": "en",
                    "timezone": "Europe/Luxembourg",
                    "host": "127.0.0.1",
                    "port": 8000,
                },
            }
        )
    )

    cfg = load_config(config_file)

    assert isinstance(cfg, AppConfig)
    assert cfg.entity.lei == "529900FSORICM1ERBP05"
    assert len(cfg.entity.authorizations) == 2
    assert cfg.entity.authorizations[0].type == "AIFM"
    assert cfg.sources["cssf_rss"].enabled is True
    assert cfg.sources["cssf_rss"].keywords == ["aif", "ucits"]
    assert cfg.llm.embedding_dim == 768


def test_load_config_rejects_unknown_authorization_type(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "entity": {
                    "lei": "X",
                    "legal_name": "X",
                    "authorizations": [{"type": "INVALID", "cssf_entity_id": "1"}],
                },
                "sources": {},
                "llm": {
                    "base_url": "x",
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
    )

    with pytest.raises(ValidationError):
        load_config(config_file)


def test_cssf_discovery_config_publication_types_loaded(tmp_path):
    cfg_text = '''
entity:
  lei: "X"
  legal_name: "Test"
  authorizations: []
sources: {}
llm:
  base_url: "http://x"
  chat_model: "x"
  embedding_model: "x"
  embedding_dim: 768
rag:
  retrieval_k: 5
  rerank_k: 3
  enable_rerank: false
  chunk_size_tokens: 100
  chunk_overlap_tokens: 10
paths:
  db_file: "x.db"
  pdf_archive: "x"
  uploads_dir: "x"
ui:
  language: en
  timezone: "Europe/Luxembourg"
  host: "127.0.0.1"
  port: 8000
cssf_discovery:
  entity_slugs:
    AIFM: aifms
    CHAPTER15_MANCO: management-companies-chapter-15
  publication_types:
    - { label: "CSSF circular", slug: circulars-cssf, type: CSSF_CIRCULAR }
    - { label: "Law", slug: laws, type: LU_LAW }
'''
    p = tmp_path / "c.yaml"
    p.write_text(cfg_text, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.cssf_discovery.entity_slugs["AIFM"] == "aifms"
    assert cfg.cssf_discovery.entity_slugs["CHAPTER15_MANCO"] == "management-companies-chapter-15"
    assert len(cfg.cssf_discovery.publication_types) == 2
    assert cfg.cssf_discovery.publication_types[0].slug == "circulars-cssf"
    assert cfg.cssf_discovery.publication_types[0].type == "CSSF_CIRCULAR"
    assert cfg.cssf_discovery.publication_types[1].slug == "laws"


def test_cssf_discovery_config_no_content_types_field():
    """The old content_types field is gone; no backward-compat shim."""
    cfg = CssfDiscoveryConfig()
    assert not hasattr(cfg, "content_types")


def test_cssf_discovery_config_rejects_unknown_keys():
    """Typos in the YAML (e.g. publication_type:) should fail loudly."""
    with pytest.raises(ValidationError):
        CssfDiscoveryConfig(publication_type=[])  # singular typo
    with pytest.raises(ValidationError):
        CssfDiscoveryConfig(entity_slug={})  # singular typo


def test_publication_type_config_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        PublicationTypeConfig(label="x", slug="y", type="z", extra_field="oops")
