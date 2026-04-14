"""Application configuration loaded from YAML."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

AuthorizationType = Literal["AIFM", "CHAPTER15_MANCO"]


class AuthorizationConfig(BaseModel):
    type: AuthorizationType
    cssf_entity_id: str


class EntityConfig(BaseModel):
    lei: str
    legal_name: str
    authorizations: list[AuthorizationConfig]


class SourceConfig(BaseModel):
    enabled: bool = True
    interval_hours: int = 6
    keywords: list[str] = Field(default_factory=list)
    celex_prefixes: list[str] = Field(default_factory=list)
    item_types: list[int] = Field(default_factory=list)
    topic_ids: list[int] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class LLMConfig(BaseModel):
    base_url: str
    chat_model: str | None = None
    embedding_model: str | None = None
    embedding_dim: int


class RagConfig(BaseModel):
    chunk_size_tokens: int
    chunk_overlap_tokens: int
    retrieval_k: int
    rerank_k: int
    enable_rerank: bool


class PathsConfig(BaseModel):
    db_file: str
    pdf_archive: str
    uploads_dir: str


class UiConfig(BaseModel):
    language: str
    timezone: str
    host: str
    port: int


class AnalysisConfig(BaseModel):
    llm_call_timeout_seconds: int = 120
    max_document_tokens: int = 24000
    max_upload_size_mb: int = 25


class AppConfig(BaseModel):
    entity: EntityConfig
    sources: dict[str, SourceConfig]
    llm: LLMConfig
    rag: RagConfig
    paths: PathsConfig
    ui: UiConfig
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)


def load_config(path: Path | str) -> AppConfig:
    """Load and validate the application config from a YAML file."""
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    return AppConfig.model_validate(raw)
