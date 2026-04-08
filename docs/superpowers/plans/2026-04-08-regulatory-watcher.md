# Regulatory Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, single-user Python tool that monitors CSSF, EU, and Luxembourg regulatory sources, persists changes with full version history, flags ICT/DORA items and drafts, and provides a browser UI with RAG-powered Q&A for Union Investment Luxembourg S.A. (LEI `529900FSORICM1ERBP05`).

**Architecture:** A FastAPI app running a five-phase ingestion pipeline (Fetch → Extract → Match → Persist → Notify) in-process alongside APScheduler, a Jinja2/HTMX/Tailwind UI, and a RAG layer backed by SQLite + `sqlite-vec` + FTS5 and a local Ollama instance. Sources are plugins behind a stable `Source` protocol; downstream phases are source-agnostic.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, SQLAlchemy 2 + Alembic, SQLite + sqlite-vec + FTS5, Jinja2 + HTMX + Tailwind (CDN), APScheduler, Typer, pytest + pytest-asyncio + pytest-httpx, Ollama (`llama3.1:8b`, `nomic-embed-text`), `feedparser`, `SPARQLWrapper`, `httpx`, `trafilatura`, `pdfplumber`, `pypdf`, `langchain-text-splitters`.

**Spec:** `docs/superpowers/specs/2026-04-08-regulatory-watcher-design.md`

---

## Phase overview

| Phase | Scope | Milestone at end |
|---|---|---|
| 1 | Foundation: project, config, DB, models, seed, CLI skeleton | `regwatch init-db && regwatch seed` works |
| 2 | Pipeline core + first source (CSSF RSS) | `regwatch run-pipeline --source cssf_rss` writes events to DB |
| 3 | Remaining sources (EU, LU, ESMA, EBA, FISMA, CSSF consultations) | All sources work from CLI |
| 4 | Ollama-based matching (references, lifecycle, semantic fallback) | Matcher handles real-world CSSF amendment text |
| 5 | RAG layer (chunking, embeddings, hybrid retrieval, answer, chat) | `regwatch chat "..."` returns cited answers |
| 6 | Scheduler | Pipeline runs on APScheduler with per-job intervals |
| 7 | Services layer | Use-case functions for the UI |
| 8 | Web UI | Browser app fully usable |
| 9 | CLI completion | All CLI commands implemented |

Each phase ends with a working, committable milestone.

---

## Phase 1 — Foundation

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `regwatch/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `config.example.yaml`
- Create: `.python-version`

- [ ] **Step 1: Initialize `pyproject.toml`**

```toml
[project]
name = "regwatch"
version = "0.1.0"
description = "Regulatory Watcher for Union Investment Luxembourg S.A."
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.27",
  "jinja2>=3.1",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "sqlite-vec>=0.1.6",
  "pydantic>=2.6",
  "pydantic-settings>=2.2",
  "pyyaml>=6.0",
  "typer>=0.12",
  "httpx>=0.27",
  "feedparser>=6.0",
  "SPARQLWrapper>=2.0",
  "trafilatura>=1.9",
  "pdfplumber>=0.11",
  "pypdf>=4.2",
  "python-dateutil>=2.9",
  "langdetect>=1.0.9",
  "langchain-text-splitters>=0.2",
  "apscheduler>=3.10",
  "python-slugify>=8.0",
  "tiktoken>=0.7",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "pytest-httpx>=0.30",
  "ruff>=0.4",
  "mypy>=1.10",
  "freezegun>=1.5",
]

[project.scripts]
regwatch = "regwatch.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["regwatch"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
  "live: live network tests, excluded from default runs",
]
addopts = "-m 'not live'"
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "N"]

[tool.mypy]
python_version = "3.11"
strict = true
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
venv/
dist/
build/
*.egg-info/
.coverage
htmlcov/

# Runtime data
/data/
config.yaml
.superpowers/

# IDE
.vscode/
.idea/
*.swp
```

- [ ] **Step 3: Create minimal `README.md`**

```markdown
# Regulatory Watcher

Local single-user tool that monitors CSSF, EU, and Luxembourg regulatory sources for Union Investment Luxembourg S.A.

See `docs/superpowers/specs/2026-04-08-regulatory-watcher-design.md` for the full design.

## Quick start

    python -m venv .venv
    . .venv/Scripts/activate   # Windows
    pip install -e .[dev]
    cp config.example.yaml config.yaml
    regwatch init-db
    regwatch seed
    uvicorn regwatch.main:app --reload

Open http://localhost:8000
```

- [ ] **Step 4: Create empty package markers**

Write `regwatch/__init__.py`:
```python
"""Regulatory Watcher package."""
__version__ = "0.1.0"
```

Write `tests/__init__.py` with a single blank line.

- [ ] **Step 5: Create `tests/conftest.py` with shared fixtures skeleton**

```python
"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory with pdfs/ and uploads/ subdirs."""
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "uploads").mkdir()
    return tmp_path
```

- [ ] **Step 6: Create `config.example.yaml`** (see Task 2 for the schema)

```yaml
entity:
  lei: "529900FSORICM1ERBP05"
  legal_name: "Union Investment Luxembourg S.A."
  authorizations:
    - type: AIFM
      cssf_entity_id: "7073800"
    - type: CHAPTER15_MANCO
      cssf_entity_id: "6918042"

sources:
  cssf_rss:
    enabled: true
    interval_hours: 6
    keywords: [aif, ucits, aml-cft, sustainable-finance, emir, mifid, investment-fund, crypto-assets]
  cssf_consultation:
    enabled: true
    interval_hours: 6
  eur_lex_adopted:
    enabled: true
    interval_hours: 6
    celex_prefixes: ["32011L0061", "32009L0065", "32022R2554", "32019R2088", "32020R0852", "32024L0927"]
  eur_lex_proposal:
    enabled: true
    interval_hours: 6
  legilux_sparql:
    enabled: true
    interval_hours: 12
  legilux_parliamentary:
    enabled: true
    interval_hours: 12
  esma_rss:
    enabled: true
    interval_hours: 6
  eba_rss:
    enabled: true
    interval_hours: 6
  ec_fisma_rss:
    enabled: true
    interval_hours: 6
    item_types: [911, 913, 916]
    topic_ids: [1565, 1588, 1601, 26362, 26363]

ollama:
  base_url: "http://localhost:11434"
  chat_model: "llama3.1:8b"
  embedding_model: "nomic-embed-text"
  embedding_dim: 768

rag:
  chunk_size_tokens: 500
  chunk_overlap_tokens: 50
  retrieval_k: 20
  rerank_k: 10
  enable_rerank: false

paths:
  db_file: "./data/app.db"
  pdf_archive: "./data/pdfs"
  uploads_dir: "./data/uploads"

ui:
  language: en
  timezone: "Europe/Luxembourg"
  host: "127.0.0.1"
  port: 8000
```

- [ ] **Step 7: Create `.python-version`**

```
3.11
```

- [ ] **Step 8: Install and verify**

Run:
```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .[dev]
pytest -q
```

Expected: `no tests ran in 0.XXs`.

- [ ] **Step 9: Commit**

```bash
git init
git add .
git commit -m "chore: initial project scaffolding"
```

### Task 2: Config module

**Files:**
- Create: `regwatch/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/__init__.py` (blank) if it doesn't exist, then `tests/unit/test_config.py`:

```python
from pathlib import Path

import yaml

from regwatch.config import AppConfig, load_config


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
                "ollama": {
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
    assert cfg.ollama.embedding_dim == 768


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
                "ollama": {
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

    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        load_config(config_file)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'regwatch.config'`.

- [ ] **Step 3: Implement `regwatch/config.py`**

```python
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


class OllamaConfig(BaseModel):
    base_url: str
    chat_model: str
    embedding_model: str
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


class AppConfig(BaseModel):
    entity: EntityConfig
    sources: dict[str, SourceConfig]
    ollama: OllamaConfig
    rag: RagConfig
    paths: PathsConfig
    ui: UiConfig


def load_config(path: Path | str) -> AppConfig:
    """Load and validate the application config from a YAML file."""
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    return AppConfig.model_validate(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_config.py -v
```
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/config.py tests/unit/__init__.py tests/unit/test_config.py
git commit -m "feat(config): add pydantic-based config loader"
```

### Task 3: Database engine with sqlite-vec and FTS5

**Files:**
- Create: `regwatch/db/__init__.py`
- Create: `regwatch/db/engine.py`
- Create: `tests/unit/test_db_engine.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_db_engine.py`:
```python
from pathlib import Path

from sqlalchemy import text

from regwatch.db.engine import create_app_engine


def test_engine_loads_sqlite_vec(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    engine = create_app_engine(db_file)

    with engine.connect() as conn:
        result = conn.execute(text("SELECT vec_version()")).scalar()
        assert result is not None
        assert isinstance(result, str)


def test_engine_enables_fts5(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    engine = create_app_engine(db_file)

    with engine.connect() as conn:
        conn.execute(text("CREATE VIRTUAL TABLE t USING fts5(content)"))
        conn.execute(text("INSERT INTO t(content) VALUES ('hello world')"))
        result = conn.execute(text("SELECT content FROM t WHERE t MATCH 'hello'")).scalar()
        assert result == "hello world"


def test_engine_enables_foreign_keys(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    engine = create_app_engine(db_file)

    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert result == 1
```

- [ ] **Step 2: Verify the failure**

```bash
pytest tests/unit/test_db_engine.py -v
```
Expected: `ModuleNotFoundError: No module named 'regwatch.db'`.

- [ ] **Step 3: Create `regwatch/db/__init__.py`** with one blank line.

- [ ] **Step 4: Implement `regwatch/db/engine.py`**

```python
"""SQLAlchemy engine factory with sqlite-vec and FTS5 loaded."""
from __future__ import annotations

from pathlib import Path

import sqlite_vec
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry


def create_app_engine(db_file: Path | str) -> Engine:
    """Create a SQLAlchemy engine against a SQLite file with sqlite-vec and FTS5 loaded."""
    db_file = Path(db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite:///{db_file.as_posix()}"
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: DBAPIConnection, _: ConnectionPoolEntry) -> None:
        # sqlite-vec requires enable_load_extension before load_extension.
        dbapi_conn.enable_load_extension(True)
        sqlite_vec.load(dbapi_conn)
        dbapi_conn.enable_load_extension(False)

        # Enable foreign keys and configure reasonable defaults.
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_db_engine.py -v
```
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add regwatch/db/__init__.py regwatch/db/engine.py tests/unit/test_db_engine.py
git commit -m "feat(db): add engine factory with sqlite-vec and FTS5"
```

### Task 4: SQLAlchemy models (all tables)

**Files:**
- Create: `regwatch/db/models.py`
- Create: `tests/unit/test_db_models.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_db_models.py`:
```python
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Authorization,
    AuthorizationType,
    Base,
    DocumentVersion,
    Entity,
    LifecycleStage,
    Regulation,
    RegulationAlias,
    RegulationApplicability,
    RegulationLifecycleLink,
    RegulationType,
    UpdateEvent,
    UpdateEventRegulationLink,
)


def _fresh_session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_create_entity_and_authorizations(tmp_path: Path) -> None:
    session = _fresh_session(tmp_path)
    entity = Entity(
        lei="529900FSORICM1ERBP05",
        legal_name="Union Investment Luxembourg S.A.",
    )
    entity.authorizations.append(
        Authorization(type=AuthorizationType.AIFM, cssf_entity_id="7073800")
    )
    entity.authorizations.append(
        Authorization(type=AuthorizationType.CHAPTER15_MANCO, cssf_entity_id="6918042")
    )
    session.add(entity)
    session.commit()

    loaded = session.get(Entity, "529900FSORICM1ERBP05")
    assert loaded is not None
    assert len(loaded.authorizations) == 2


def test_regulation_with_alias_and_applicability(tmp_path: Path) -> None:
    session = _fresh_session(tmp_path)
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 18/698",
        title="IFM authorisation and organisation",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://www.cssf.lu/en/Document/circular-cssf-18-698/",
    )
    reg.aliases.append(RegulationAlias(pattern=r"CSSF[\s\-]?18[/\-]698", kind="REGEX"))
    reg.applicabilities.append(RegulationApplicability(authorization_type="BOTH"))
    session.add(reg)
    session.commit()

    loaded = session.scalars(Regulation.__table__.select()).first()
    assert loaded is not None


def test_document_version_is_current_flag(tmp_path: Path) -> None:
    session = _fresh_session(tmp_path)
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 18/698",
        title="IFM",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()

    v1 = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=1,
        is_current=True,
        fetched_at=datetime.now(timezone.utc),
        source_url="https://example.com/v1",
        content_hash="a" * 64,
        html_text="original",
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(v1)
    session.commit()

    assert v1.version_id is not None


def test_update_event_matches_multiple_regulations(tmp_path: Path) -> None:
    session = _fresh_session(tmp_path)
    reg1 = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 18/698",
        title="A",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com/a",
    )
    reg2 = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 22/806",
        title="B",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com/b",
    )
    session.add_all([reg1, reg2])
    session.flush()

    ev = UpdateEvent(
        source="CSSF_RSS",
        source_url="https://example.com/new",
        title="New circular amending 18/698 and 22/806",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        raw_payload={},
        content_hash="b" * 64,
        severity="MATERIAL",
        review_status="NEW",
    )
    ev.regulation_links.append(
        UpdateEventRegulationLink(
            regulation_id=reg1.regulation_id,
            match_method="REGEX_ALIAS",
            confidence=1.0,
        )
    )
    ev.regulation_links.append(
        UpdateEventRegulationLink(
            regulation_id=reg2.regulation_id,
            match_method="REGEX_ALIAS",
            confidence=1.0,
        )
    )
    session.add(ev)
    session.commit()

    loaded = session.get(UpdateEvent, ev.event_id)
    assert loaded is not None
    assert len(loaded.regulation_links) == 2


def test_regulation_lifecycle_link(tmp_path: Path) -> None:
    session = _fresh_session(tmp_path)
    proposal = Regulation(
        type=RegulationType.EU_DIRECTIVE,
        reference_number="COM/2021/721",
        celex_id="52021PC0721",
        title="Proposal for AIFMD II",
        issuing_authority="European Commission",
        lifecycle_stage=LifecycleStage.PROPOSAL,
        is_ict=False,
        source_of_truth="DISCOVERED",
        url="https://example.com/prop",
    )
    adopted = Regulation(
        type=RegulationType.EU_DIRECTIVE,
        reference_number="Directive 2024/927",
        celex_id="32024L0927",
        title="AIFMD II",
        issuing_authority="European Parliament",
        lifecycle_stage=LifecycleStage.ADOPTED_NOT_IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com/adopted",
    )
    session.add_all([proposal, adopted])
    session.flush()

    link = RegulationLifecycleLink(
        from_regulation_id=proposal.regulation_id,
        to_regulation_id=adopted.regulation_id,
        relation="PROPOSAL_OF",
    )
    session.add(link)
    session.commit()

    assert link.link_id is not None
```

- [ ] **Step 2: Verify the failure**

```bash
pytest tests/unit/test_db_models.py -v
```
Expected: `ModuleNotFoundError: No module named 'regwatch.db.models'`.

- [ ] **Step 3: Implement `regwatch/db/models.py`**

```python
"""SQLAlchemy ORM models for the Regulatory Watcher database."""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AuthorizationType(str, enum.Enum):
    AIFM = "AIFM"
    CHAPTER15_MANCO = "CHAPTER15_MANCO"


class RegulationType(str, enum.Enum):
    LU_LAW = "LU_LAW"
    CSSF_CIRCULAR = "CSSF_CIRCULAR"
    CSSF_REGULATION = "CSSF_REGULATION"
    EU_REGULATION = "EU_REGULATION"
    EU_DIRECTIVE = "EU_DIRECTIVE"
    ESMA_GUIDELINE = "ESMA_GUIDELINE"
    RTS = "RTS"
    ITS = "ITS"
    DELEGATED_ACT = "DELEGATED_ACT"


class LifecycleStage(str, enum.Enum):
    CONSULTATION = "CONSULTATION"
    PROPOSAL = "PROPOSAL"
    DRAFT_BILL = "DRAFT_BILL"
    ADOPTED_NOT_IN_FORCE = "ADOPTED_NOT_IN_FORCE"
    IN_FORCE = "IN_FORCE"
    AMENDED = "AMENDED"
    REPEALED = "REPEALED"


class DoraPillar(str, enum.Enum):
    ICT_RISK_MGMT = "ICT_RISK_MGMT"
    INCIDENT_REPORTING = "INCIDENT_REPORTING"
    RESILIENCE_TESTING = "RESILIENCE_TESTING"
    THIRD_PARTY_RISK = "THIRD_PARTY_RISK"
    INFO_SHARING = "INFO_SHARING"


class Entity(Base):
    __tablename__ = "entity"

    lei: Mapped[str] = mapped_column(String(20), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(255))
    rcs_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nace_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    gleif_last_updated: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    authorizations: Mapped[list[Authorization]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class Authorization(Base):
    __tablename__ = "authorization"

    authorization_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lei: Mapped[str] = mapped_column(ForeignKey("entity.lei"))
    type: Mapped[AuthorizationType] = mapped_column(Enum(AuthorizationType))
    cssf_entity_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    authorization_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cssf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    entity: Mapped[Entity] = relationship(back_populates="authorizations")

    __table_args__ = (UniqueConstraint("lei", "type", name="uq_authorization_lei_type"),)


class Regulation(Base):
    __tablename__ = "regulation"

    regulation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[RegulationType] = mapped_column(Enum(RegulationType))
    reference_number: Mapped[str] = mapped_column(String(100), index=True)
    celex_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    eli_uri: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    issuing_authority: Mapped[str] = mapped_column(String(100))
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    lifecycle_stage: Mapped[LifecycleStage] = mapped_column(Enum(LifecycleStage))
    transposition_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    application_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_ict: Mapped[bool] = mapped_column(Boolean, default=False)
    dora_pillar: Mapped[DoraPillar | None] = mapped_column(Enum(DoraPillar), nullable=True)
    url: Mapped[str] = mapped_column(String(500))
    source_of_truth: Mapped[str] = mapped_column(String(20))  # SEED / DISCOVERED
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("regulation.regulation_id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    aliases: Mapped[list[RegulationAlias]] = relationship(
        back_populates="regulation", cascade="all, delete-orphan"
    )
    applicabilities: Mapped[list[RegulationApplicability]] = relationship(
        back_populates="regulation", cascade="all, delete-orphan"
    )
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="regulation", cascade="all, delete-orphan"
    )


class RegulationAlias(Base):
    __tablename__ = "regulation_alias"

    alias_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.regulation_id"))
    pattern: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20))  # EXACT / REGEX / CELEX / ELI

    regulation: Mapped[Regulation] = relationship(back_populates="aliases")


class RegulationApplicability(Base):
    __tablename__ = "regulation_applicability"

    applicability_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.regulation_id"))
    authorization_type: Mapped[str] = mapped_column(String(20))  # AIFM / CHAPTER15_MANCO / BOTH
    scope_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    regulation: Mapped[Regulation] = relationship(back_populates="applicabilities")


class RegulationLifecycleLink(Base):
    __tablename__ = "regulation_lifecycle_link"

    link_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.regulation_id"))
    to_regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.regulation_id"))
    relation: Mapped[str] = mapped_column(String(20))  # PROPOSAL_OF / TRANSPOSES / AMENDS / REPEALS / SUCCEEDS


class DocumentVersion(Base):
    __tablename__ = "document_version"

    version_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.regulation_id"))
    version_number: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    source_url: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    html_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    pdf_manual_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    regulation: Mapped[Regulation] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "regulation_id", "version_number", name="uq_document_version_regulation_version"
        ),
    )


class UpdateEvent(Base):
    __tablename__ = "update_event"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_ict: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    severity: Mapped[str] = mapped_column(String(20))  # INFORMATIONAL / MATERIAL / CRITICAL
    review_status: Mapped[str] = mapped_column(
        String(20), default="NEW", index=True
    )  # NEW / SEEN / ASSESSED / ARCHIVED
    seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    regulation_links: Mapped[list[UpdateEventRegulationLink]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class UpdateEventRegulationLink(Base):
    __tablename__ = "update_event_regulation_link"

    link_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("update_event.event_id"))
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.regulation_id"))
    match_method: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)
    matched_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped[UpdateEvent] = relationship(back_populates="regulation_links")


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # RUNNING / COMPLETED / FAILED / ABORTED
    sources_attempted: Mapped[list[str]] = mapped_column(JSON, default=list)
    sources_failed: Mapped[list[str]] = mapped_column(JSON, default=list)
    events_created: Mapped[int] = mapped_column(Integer, default=0)
    versions_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunk"

    chunk_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_version.version_id"), index=True)
    regulation_id: Mapped[int] = mapped_column(ForeignKey("regulation.regulation_id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    lifecycle_stage: Mapped[str] = mapped_column(String(30))
    is_ict: Mapped[bool] = mapped_column(Boolean, default=False)
    authorization_types: Mapped[list[str]] = mapped_column(JSON, default=list)


class ChatSession(Base):
    __tablename__ = "chat_session"

    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_message"

    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_session.session_id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user / assistant / system
    content: Mapped[str] = mapped_column(Text)
    retrieved_chunk_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_db_models.py -v
```
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/db/models.py tests/unit/test_db_models.py
git commit -m "feat(db): add SQLAlchemy ORM models"
```

### Task 5: Virtual tables bootstrap (sqlite-vec and FTS5)

SQLAlchemy's declarative `Base.metadata.create_all()` does not cover virtual tables. They need raw DDL. This task writes a small helper that creates the virtual tables and is idempotent.

**Files:**
- Create: `regwatch/db/virtual_tables.py`
- Create: `tests/unit/test_virtual_tables.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from sqlalchemy import text

from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base
from regwatch.db.virtual_tables import create_virtual_tables


def test_creates_vec_and_fts_tables(tmp_path: Path) -> None:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=768)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ).scalars().all()
        assert "document_chunk_vec" in rows
        assert "document_chunk_fts" in rows


def test_create_virtual_tables_is_idempotent(tmp_path: Path) -> None:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=768)
    # Second call must not raise.
    create_virtual_tables(engine, embedding_dim=768)
```

- [ ] **Step 2: Verify the failure**

```bash
pytest tests/unit/test_virtual_tables.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `regwatch/db/virtual_tables.py`**

```python
"""Create virtual tables that SQLAlchemy declarative does not manage."""
from __future__ import annotations

from sqlalchemy import Engine, text


def create_virtual_tables(engine: Engine, *, embedding_dim: int) -> None:
    """Create `document_chunk_vec` (sqlite-vec) and `document_chunk_fts` (FTS5) if missing."""
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunk_vec USING vec0(
                    chunk_id INTEGER PRIMARY KEY,
                    embedding float[{embedding_dim}]
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunk_fts USING fts5(
                    text,
                    content='document_chunk',
                    content_rowid='chunk_id'
                )
                """
            )
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_virtual_tables.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/db/virtual_tables.py tests/unit/test_virtual_tables.py
git commit -m "feat(db): add virtual table bootstrap for sqlite-vec and FTS5"
```

### Task 6: Alembic initial migration

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/` (empty dir, create with a `.keep` file)

- [ ] **Step 1: Initialize Alembic**

```bash
alembic init alembic
```

This creates `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, and `alembic/versions/`.

- [ ] **Step 2: Edit `alembic.ini`**

Set `sqlalchemy.url` to a placeholder (it will be overridden from env.py):
```ini
sqlalchemy.url = sqlite:///./data/app.db
```

- [ ] **Step 3: Replace `alembic/env.py`**

```python
"""Alembic environment configured against our engine factory."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from regwatch.config import load_config
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_engine():
    app_config = load_config("config.yaml")
    return create_app_engine(app_config.paths.db_file)


def run_migrations_offline() -> None:
    engine = _get_engine()
    url = str(engine.url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = _get_engine()
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate the initial migration**

Copy `config.example.yaml` to `config.yaml` first so env.py can load it:

```bash
cp config.example.yaml config.yaml
mkdir -p data
alembic revision --autogenerate -m "initial schema"
```

This creates a file at `alembic/versions/<hash>_initial_schema.py`.

- [ ] **Step 5: Apply the migration**

```bash
alembic upgrade head
```

Expected: no error. Check `data/app.db` exists.

- [ ] **Step 6: Commit**

```bash
git add alembic alembic.ini
git commit -m "feat(db): add alembic initial migration"
```

### Task 7: Seed catalog YAML and loader

**Files:**
- Create: `seeds/regulations_seed.yaml`
- Create: `regwatch/db/seed.py`
- Create: `tests/unit/test_seed_loader.py`

- [ ] **Step 1: Create `seeds/regulations_seed.yaml`** with a curated set of ~50 regulations. Start with these representative entries (the implementer expands to the full list from the research document):

```yaml
# Curated seed of the regulatory catalog for Union Investment Luxembourg S.A.
# Extend as needed. source_of_truth is implicitly SEED for every entry in this file.

entity:
  lei: "529900FSORICM1ERBP05"
  legal_name: "Union Investment Luxembourg S.A."
  rcs_number: "B28679"
  address: "3 Heienhaff, L-1736 Senningerberg, Luxembourg"
  jurisdiction: "LU"
  nace_code: "66.30"

authorizations:
  - type: AIFM
    cssf_entity_id: "7073800"
  - type: CHAPTER15_MANCO
    cssf_entity_id: "6918042"

regulations:
  - reference_number: "CSSF 18/698"
    type: CSSF_CIRCULAR
    title: "IFM authorisation, organisation, governance, substance and delegation"
    issuing_authority: "CSSF"
    publication_date: 2018-08-23
    lifecycle_stage: IN_FORCE
    is_ict: false
    url: "https://www.cssf.lu/en/Document/circular-cssf-18-698/"
    applicability: BOTH
    aliases:
      - { pattern: 'CSSF[\s\-]?18[/\-]698', kind: REGEX }
      - { pattern: 'Circular 18/698', kind: EXACT }

  - reference_number: "CSSF 23/844"
    type: CSSF_CIRCULAR
    title: "AIFM reporting obligations under Article 24 AIFMD"
    issuing_authority: "CSSF"
    publication_date: 2023-11-20
    lifecycle_stage: IN_FORCE
    is_ict: false
    url: "https://www.cssf.lu/en/Document/circular-cssf-23-844/"
    applicability: AIFM_ONLY
    aliases:
      - { pattern: 'CSSF[\s\-]?23[/\-]844', kind: REGEX }

  - reference_number: "CSSF 11/512"
    type: CSSF_CIRCULAR
    title: "Risk management clarifications for UCITS ManCos"
    issuing_authority: "CSSF"
    publication_date: 2011-05-30
    lifecycle_stage: IN_FORCE
    is_ict: false
    url: "https://www.cssf.lu/en/Document/circular-cssf-11-512/"
    applicability: MANCO_ONLY
    aliases:
      - { pattern: 'CSSF[\s\-]?11[/\-]512', kind: REGEX }

  - reference_number: "CSSF 24/856"
    type: CSSF_CIRCULAR
    title: "NAV errors and breach of investment rules (replacing 02/77)"
    issuing_authority: "CSSF"
    publication_date: 2024-01-01
    effective_date: 2025-01-01
    lifecycle_stage: IN_FORCE
    is_ict: false
    url: "https://www.cssf.lu/en/Document/circular-cssf-24-856/"
    applicability: BOTH
    aliases:
      - { pattern: 'CSSF[\s\-]?24[/\-]856', kind: REGEX }

  - reference_number: "Directive 2011/61/EU"
    type: EU_DIRECTIVE
    celex_id: "32011L0061"
    title: "Alternative Investment Fund Managers Directive (AIFMD)"
    issuing_authority: "European Parliament"
    publication_date: 2011-06-08
    lifecycle_stage: IN_FORCE
    is_ict: false
    url: "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32011L0061"
    applicability: AIFM_ONLY
    aliases:
      - { pattern: 'AIFMD', kind: EXACT }
      - { pattern: '32011L0061', kind: CELEX }
      - { pattern: 'Directive 2011/61/EU', kind: EXACT }

  - reference_number: "Directive 2009/65/EC"
    type: EU_DIRECTIVE
    celex_id: "32009L0065"
    title: "Undertakings for Collective Investment in Transferable Securities (UCITS)"
    issuing_authority: "European Parliament"
    publication_date: 2009-07-13
    lifecycle_stage: IN_FORCE
    is_ict: false
    url: "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32009L0065"
    applicability: MANCO_ONLY
    aliases:
      - { pattern: 'UCITS Directive', kind: EXACT }
      - { pattern: '32009L0065', kind: CELEX }
      - { pattern: 'Directive 2009/65/EC', kind: EXACT }

  - reference_number: "Directive (EU) 2024/927"
    type: EU_DIRECTIVE
    celex_id: "32024L0927"
    title: "AIFMD II"
    issuing_authority: "European Parliament"
    publication_date: 2024-03-26
    transposition_deadline: 2026-04-16
    application_date: 2027-04-16
    lifecycle_stage: ADOPTED_NOT_IN_FORCE
    is_ict: false
    url: "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024L0927"
    applicability: BOTH
    aliases:
      - { pattern: 'AIFMD II', kind: EXACT }
      - { pattern: '32024L0927', kind: CELEX }
      - { pattern: 'Directive \(EU\) 2024/927', kind: REGEX }

  - reference_number: "Regulation (EU) 2022/2554"
    type: EU_REGULATION
    celex_id: "32022R2554"
    title: "Digital Operational Resilience Act (DORA)"
    issuing_authority: "European Parliament"
    publication_date: 2022-12-14
    application_date: 2025-01-17
    lifecycle_stage: IN_FORCE
    is_ict: true
    url: "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32022R2554"
    applicability: BOTH
    aliases:
      - { pattern: 'DORA', kind: EXACT }
      - { pattern: '32022R2554', kind: CELEX }
      - { pattern: 'Regulation \(EU\) 2022/2554', kind: REGEX }

  - reference_number: "Regulation (EU) 2019/2088"
    type: EU_REGULATION
    celex_id: "32019R2088"
    title: "Sustainable Finance Disclosure Regulation (SFDR)"
    issuing_authority: "European Parliament"
    publication_date: 2019-11-27
    lifecycle_stage: IN_FORCE
    is_ict: false
    url: "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32019R2088"
    applicability: BOTH
    aliases:
      - { pattern: 'SFDR', kind: EXACT }
      - { pattern: '32019R2088', kind: CELEX }

  # ... expand to cover the ~50 regulations listed in the research section of the spec.
```

**Note to implementer:** Extend this file to cover every regulation in Section 1–3 of the research block in `initialPrompt.txt` and the main spec document. Every row needs a `reference_number`, `type`, `title`, `issuing_authority`, `lifecycle_stage`, `is_ict`, `url`, `applicability`, and at least one alias. Dates may be omitted if unknown.

- [ ] **Step 2: Write the failing test**

`tests/unit/test_seed_loader.py`:
```python
from pathlib import Path
from textwrap import dedent

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Authorization,
    Base,
    Entity,
    Regulation,
    RegulationAlias,
    RegulationApplicability,
)
from regwatch.db.seed import load_seed


def test_load_seed_populates_entity_and_regulations(tmp_path: Path) -> None:
    seed_file = tmp_path / "seed.yaml"
    seed_file.write_text(
        dedent(
            """
            entity:
              lei: "TEST1234567890123456"
              legal_name: "Test Entity"
              rcs_number: "B1"
              address: "A"
              jurisdiction: "LU"
              nace_code: "66.30"

            authorizations:
              - type: AIFM
                cssf_entity_id: "1"
              - type: CHAPTER15_MANCO
                cssf_entity_id: "2"

            regulations:
              - reference_number: "CSSF 18/698"
                type: CSSF_CIRCULAR
                title: "IFM governance"
                issuing_authority: "CSSF"
                lifecycle_stage: IN_FORCE
                is_ict: false
                url: "https://example.com"
                applicability: BOTH
                aliases:
                  - { pattern: "CSSF 18/698", kind: EXACT }
            """
        )
    )

    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        load_seed(session, seed_file)
        session.commit()

    with Session(engine) as session:
        entity = session.get(Entity, "TEST1234567890123456")
        assert entity is not None
        assert len(entity.authorizations) == 2

        regs = session.query(Regulation).all()
        assert len(regs) == 1
        assert regs[0].reference_number == "CSSF 18/698"
        assert regs[0].source_of_truth == "SEED"

        aliases = session.query(RegulationAlias).all()
        assert len(aliases) == 1
        assert aliases[0].pattern == "CSSF 18/698"


def test_load_seed_is_idempotent(tmp_path: Path) -> None:
    seed_file = tmp_path / "seed.yaml"
    seed_file.write_text(
        dedent(
            """
            entity:
              lei: "TEST1234567890123456"
              legal_name: "Test Entity"
              rcs_number: "B1"
              address: "A"
              jurisdiction: "LU"
              nace_code: "66.30"
            authorizations:
              - type: AIFM
                cssf_entity_id: "1"
            regulations:
              - reference_number: "CSSF 18/698"
                type: CSSF_CIRCULAR
                title: "X"
                issuing_authority: "CSSF"
                lifecycle_stage: IN_FORCE
                is_ict: false
                url: "https://example.com"
                applicability: BOTH
                aliases:
                  - { pattern: "CSSF 18/698", kind: EXACT }
            """
        )
    )

    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        load_seed(session, seed_file)
        load_seed(session, seed_file)
        session.commit()

    with Session(engine) as session:
        assert session.query(Regulation).count() == 1
        assert session.query(RegulationAlias).count() == 1
```

- [ ] **Step 3: Verify failure**

```bash
pytest tests/unit/test_seed_loader.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement `regwatch/db/seed.py`**

```python
"""Seed loader: reads a curated YAML file into the regulatory database."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from regwatch.db.models import (
    Authorization,
    AuthorizationType,
    Entity,
    LifecycleStage,
    Regulation,
    RegulationAlias,
    RegulationApplicability,
    RegulationType,
)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_seed(session: Session, seed_path: Path | str) -> None:
    """Load or upsert the curated seed from a YAML file.

    The loader is idempotent: running it twice does not create duplicates.
    Existing rows with the same natural key (LEI for entity, reference_number for
    regulation) are updated in place; new rows are inserted.
    """
    data = yaml.safe_load(Path(seed_path).read_text(encoding="utf-8"))

    entity_data = data["entity"]
    entity = session.get(Entity, entity_data["lei"])
    if entity is None:
        entity = Entity(lei=entity_data["lei"], legal_name=entity_data["legal_name"])
        session.add(entity)
    entity.legal_name = entity_data["legal_name"]
    entity.rcs_number = entity_data.get("rcs_number")
    entity.address = entity_data.get("address")
    entity.jurisdiction = entity_data.get("jurisdiction")
    entity.nace_code = entity_data.get("nace_code")

    session.flush()

    existing_auth = {a.type.value: a for a in entity.authorizations}
    for auth_data in data.get("authorizations", []):
        auth_type = auth_data["type"]
        if auth_type in existing_auth:
            auth = existing_auth[auth_type]
        else:
            auth = Authorization(lei=entity.lei, type=AuthorizationType(auth_type))
            entity.authorizations.append(auth)
        auth.cssf_entity_id = auth_data.get("cssf_entity_id")

    session.flush()

    for reg_data in data.get("regulations", []):
        _upsert_regulation(session, reg_data)


def _upsert_regulation(session: Session, reg_data: dict[str, Any]) -> None:
    reference = reg_data["reference_number"]
    reg = (
        session.query(Regulation)
        .filter(Regulation.reference_number == reference)
        .one_or_none()
    )
    if reg is None:
        reg = Regulation(
            reference_number=reference,
            source_of_truth="SEED",
            type=RegulationType(reg_data["type"]),
            title=reg_data["title"],
            issuing_authority=reg_data["issuing_authority"],
            lifecycle_stage=LifecycleStage(reg_data["lifecycle_stage"]),
            is_ict=reg_data.get("is_ict", False),
            url=reg_data["url"],
        )
        session.add(reg)
    else:
        reg.type = RegulationType(reg_data["type"])
        reg.title = reg_data["title"]
        reg.issuing_authority = reg_data["issuing_authority"]
        reg.lifecycle_stage = LifecycleStage(reg_data["lifecycle_stage"])
        reg.is_ict = reg_data.get("is_ict", False)
        reg.url = reg_data["url"]

    reg.celex_id = reg_data.get("celex_id")
    reg.eli_uri = reg_data.get("eli_uri")
    reg.publication_date = _parse_date(reg_data.get("publication_date"))
    reg.effective_date = _parse_date(reg_data.get("effective_date"))
    reg.transposition_deadline = _parse_date(reg_data.get("transposition_deadline"))
    reg.application_date = _parse_date(reg_data.get("application_date"))

    session.flush()

    # Replace aliases in place.
    session.query(RegulationAlias).filter(
        RegulationAlias.regulation_id == reg.regulation_id
    ).delete()
    for alias_data in reg_data.get("aliases", []):
        session.add(
            RegulationAlias(
                regulation_id=reg.regulation_id,
                pattern=alias_data["pattern"],
                kind=alias_data["kind"],
            )
        )

    # Replace applicabilities.
    session.query(RegulationApplicability).filter(
        RegulationApplicability.regulation_id == reg.regulation_id
    ).delete()
    app = reg_data.get("applicability", "BOTH")
    if app == "AIFM_ONLY":
        types = ["AIFM"]
    elif app == "MANCO_ONLY":
        types = ["CHAPTER15_MANCO"]
    else:
        types = ["BOTH"]
    for t in types:
        session.add(
            RegulationApplicability(regulation_id=reg.regulation_id, authorization_type=t)
        )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_seed_loader.py -v
```
Expected: 2 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add seeds/regulations_seed.yaml regwatch/db/seed.py tests/unit/test_seed_loader.py
git commit -m "feat(db): add seed catalog loader with idempotent upsert"
```

### Task 8: CLI skeleton with `init-db` and `seed`

**Files:**
- Create: `regwatch/cli.py`
- Create: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from regwatch.cli import app

runner = CliRunner()


def _minimal_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.yaml"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "pdfs").mkdir()
    (data_dir / "uploads").mkdir()
    config_file.write_text(
        dedent(
            f"""
            entity:
              lei: "TEST1234567890123456"
              legal_name: "Test"
              authorizations:
                - type: AIFM
                  cssf_entity_id: "1"
            sources: {{}}
            ollama:
              base_url: "http://localhost:11434"
              chat_model: "llama3.1:8b"
              embedding_model: "nomic-embed-text"
              embedding_dim: 768
            rag:
              chunk_size_tokens: 500
              chunk_overlap_tokens: 50
              retrieval_k: 20
              rerank_k: 10
              enable_rerank: false
            paths:
              db_file: "{(data_dir / 'app.db').as_posix()}"
              pdf_archive: "{(data_dir / 'pdfs').as_posix()}"
              uploads_dir: "{(data_dir / 'uploads').as_posix()}"
            ui:
              language: en
              timezone: "Europe/Luxembourg"
              host: "127.0.0.1"
              port: 8000
            """
        )
    )
    return config_file


def test_init_db_creates_schema(tmp_path: Path) -> None:
    config_file = _minimal_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config_file), "init-db"])
    assert result.exit_code == 0, result.output
    db_file = tmp_path / "data" / "app.db"
    assert db_file.exists()


def test_seed_loads_catalog(tmp_path: Path) -> None:
    config_file = _minimal_config(tmp_path)
    seed_file = tmp_path / "seed.yaml"
    seed_file.write_text(
        dedent(
            """
            entity:
              lei: "TEST1234567890123456"
              legal_name: "Test"
            authorizations:
              - type: AIFM
                cssf_entity_id: "1"
            regulations:
              - reference_number: "CSSF 18/698"
                type: CSSF_CIRCULAR
                title: "IFM"
                issuing_authority: "CSSF"
                lifecycle_stage: IN_FORCE
                is_ict: false
                url: "https://example.com"
                applicability: BOTH
                aliases:
                  - { pattern: "CSSF 18/698", kind: EXACT }
            """
        )
    )
    runner.invoke(app, ["--config", str(config_file), "init-db"])
    result = runner.invoke(
        app, ["--config", str(config_file), "seed", "--file", str(seed_file)]
    )
    assert result.exit_code == 0, result.output
    assert "1 regulation" in result.output.lower() or "loaded" in result.output.lower()
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/unit/test_cli.py -v
```
Expected: `ModuleNotFoundError: No module named 'regwatch.cli'`.

- [ ] **Step 3: Implement `regwatch/cli.py`**

```python
"""Typer-based CLI for the Regulatory Watcher."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from regwatch.config import AppConfig, load_config
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, Regulation
from regwatch.db.seed import load_seed
from regwatch.db.virtual_tables import create_virtual_tables

app = typer.Typer(help="Regulatory Watcher CLI.")


class _State:
    config: AppConfig | None = None


_state = _State()


@app.callback()
def main(
    config: Annotated[
        Path, typer.Option("--config", "-c", help="Path to config.yaml")
    ] = Path("config.yaml"),
) -> None:
    """Load the configuration for the invoked command."""
    _state.config = load_config(config)


def _get_config() -> AppConfig:
    if _state.config is None:
        raise RuntimeError("Config not loaded")
    return _state.config


@app.command("init-db")
def init_db() -> None:
    """Create the database schema and virtual tables."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=cfg.ollama.embedding_dim)
    typer.echo(f"Schema created in {cfg.paths.db_file}")


@app.command("seed")
def seed(
    file: Annotated[
        Path, typer.Option("--file", "-f", help="Path to the seed YAML")
    ] = Path("seeds/regulations_seed.yaml"),
) -> None:
    """Load the curated seed catalog into the database."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as session:
        load_seed(session, file)
        session.commit()
        count = session.query(Regulation).count()
    typer.echo(f"Loaded seed. {count} regulation(s) in the catalog.")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_cli.py -v
```
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add init-db and seed commands"
```

---

## Phase 2 — Pipeline core + first source (CSSF RSS)

### Task 9: Domain dataclasses

**Files:**
- Create: `regwatch/domain/__init__.py`
- Create: `regwatch/domain/types.py`
- Create: `tests/unit/test_domain_types.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone

from regwatch.domain.types import (
    ExtractedDocument,
    MatchedDocument,
    MatchedReference,
    RawDocument,
)


def test_raw_document_dataclass() -> None:
    d = RawDocument(
        source="cssf_rss",
        source_url="https://example.com",
        title="Test",
        published_at=datetime(2026, 4, 8, tzinfo=timezone.utc),
        raw_payload={"guid": "abc"},
        fetched_at=datetime(2026, 4, 8, tzinfo=timezone.utc),
    )
    assert d.title == "Test"
    assert d.raw_payload["guid"] == "abc"


def test_extracted_document_carries_raw() -> None:
    raw = RawDocument(
        source="x",
        source_url="https://x",
        title="t",
        published_at=datetime.now(timezone.utc),
        raw_payload={},
        fetched_at=datetime.now(timezone.utc),
    )
    ext = ExtractedDocument(
        raw=raw,
        html_text="body",
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    assert ext.raw.source == "x"
    assert ext.html_text == "body"


def test_matched_document_contains_references() -> None:
    raw = RawDocument(
        source="x",
        source_url="https://x",
        title="t",
        published_at=datetime.now(timezone.utc),
        raw_payload={},
        fetched_at=datetime.now(timezone.utc),
    )
    ext = ExtractedDocument(
        raw=raw,
        html_text=None,
        pdf_path=None,
        pdf_extracted_text="text mentions CSSF 18/698",
        pdf_is_protected=False,
    )
    matched = MatchedDocument(
        extracted=ext,
        references=[
            MatchedReference(
                regulation_id=42,
                method="REGEX_ALIAS",
                confidence=1.0,
                snippet="CSSF 18/698",
            )
        ],
        lifecycle_stage="IN_FORCE",
        is_ict=False,
        severity="MATERIAL",
    )
    assert len(matched.references) == 1
    assert matched.references[0].regulation_id == 42
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/unit/test_domain_types.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `regwatch/domain/__init__.py`** (blank) and `regwatch/domain/types.py`:

```python
"""Pipeline domain dataclasses passed between phases."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawDocument:
    """Raw item as returned by a Source plugin. Text content is NOT loaded yet."""

    source: str
    source_url: str
    title: str
    published_at: datetime
    raw_payload: dict[str, Any]
    fetched_at: datetime


@dataclass
class ExtractedDocument:
    """A RawDocument plus its extracted text content (HTML and/or PDF)."""

    raw: RawDocument
    html_text: str | None
    pdf_path: str | None
    pdf_extracted_text: str | None
    pdf_is_protected: bool


@dataclass
class MatchedReference:
    """One match between a document and a catalog regulation."""

    regulation_id: int
    method: str  # REGEX_ALIAS / CELEX_ID / ELI_URI / OLLAMA_REFERENCE / OLLAMA_SEMANTIC / MANUAL
    confidence: float
    snippet: str | None = None


@dataclass
class MatchedDocument:
    """An ExtractedDocument plus its matched regulations and classifications."""

    extracted: ExtractedDocument
    references: list[MatchedReference] = field(default_factory=list)
    lifecycle_stage: str = "IN_FORCE"
    is_ict: bool = False
    severity: str = "INFORMATIONAL"
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_domain_types.py -v
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/domain tests/unit/test_domain_types.py
git commit -m "feat(domain): add pipeline domain dataclasses"
```

### Task 10: Source protocol and registry

**Files:**
- Create: `regwatch/pipeline/__init__.py`
- Create: `regwatch/pipeline/fetch/__init__.py`
- Create: `regwatch/pipeline/fetch/base.py`
- Create: `tests/unit/test_source_registry.py`

- [ ] **Step 1: Write the failing test**

```python
from collections.abc import Iterator
from datetime import datetime, timezone

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import REGISTRY, Source, register_source


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
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/unit/test_source_registry.py -v
```

- [ ] **Step 3: Implement**

`regwatch/pipeline/__init__.py` — blank.
`regwatch/pipeline/fetch/__init__.py` — blank.
`regwatch/pipeline/fetch/base.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_source_registry.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/pipeline tests/unit/test_source_registry.py
git commit -m "feat(pipeline): add Source protocol and registry"
```

### Task 11: CSSF RSS source with fixture-based tests

**Files:**
- Create: `regwatch/pipeline/fetch/cssf_rss.py`
- Create: `tests/fixtures/cssf_rss_sample.xml`
- Create: `tests/unit/test_cssf_rss_source.py`

- [ ] **Step 1: Create the fixture file** `tests/fixtures/cssf_rss_sample.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CSSF Publications - aif</title>
    <link>https://www.cssf.lu/en/feed/publications?content_keyword=aif</link>
    <description>CSSF publications filtered by keyword aif</description>
    <item>
      <title>Circular CSSF 25/901 on ICT outsourcing notifications</title>
      <link>https://www.cssf.lu/en/Document/circular-cssf-25-901/</link>
      <description>&lt;p&gt;This circular amends Circular CSSF 25/882 on ICT outsourcing notifications...&lt;/p&gt;</description>
      <pubDate>Mon, 06 Apr 2026 09:30:00 +0200</pubDate>
      <guid isPermaLink="true">https://www.cssf.lu/en/Document/circular-cssf-25-901/</guid>
    </item>
    <item>
      <title>Updated FAQ on AIFMD reporting</title>
      <link>https://www.cssf.lu/en/Document/faq-aifmd-reporting-update/</link>
      <description>Updates to the FAQ on AIFMD reporting.</description>
      <pubDate>Fri, 03 Apr 2026 16:00:00 +0200</pubDate>
      <guid isPermaLink="true">https://www.cssf.lu/en/Document/faq-aifmd-reporting-update/</guid>
    </item>
  </channel>
</rss>
```

- [ ] **Step 2: Write the failing test**

`tests/unit/test_cssf_rss_source.py`:
```python
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from regwatch.pipeline.fetch.cssf_rss import CssfRssSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cssf_rss_sample.xml"


def test_fetch_parses_items(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications?content_keyword=aif",
        content=FIXTURE.read_bytes(),
        headers={"content-type": "application/rss+xml"},
    )

    source = CssfRssSource(keywords=["aif"])
    since = datetime(2020, 1, 1, tzinfo=timezone.utc)
    items = list(source.fetch(since))

    assert len(items) == 2
    assert items[0].source == "cssf_rss"
    assert items[0].title == "Circular CSSF 25/901 on ICT outsourcing notifications"
    assert items[0].source_url == "https://www.cssf.lu/en/Document/circular-cssf-25-901/"
    assert items[0].published_at.tzinfo is not None


def test_fetch_filters_by_since(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications?content_keyword=aif",
        content=FIXTURE.read_bytes(),
        headers={"content-type": "application/rss+xml"},
    )
    source = CssfRssSource(keywords=["aif"])
    since = datetime(2026, 4, 5, tzinfo=timezone.utc)
    items = list(source.fetch(since))
    assert len(items) == 1
    assert "25/901" in items[0].title


def test_fetch_combines_multiple_keywords(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications?content_keyword=aif",
        content=FIXTURE.read_bytes(),
    )
    httpx_mock.add_response(
        url="https://www.cssf.lu/en/feed/publications?content_keyword=ucits",
        content=FIXTURE.read_bytes(),
    )
    source = CssfRssSource(keywords=["aif", "ucits"])
    items = list(source.fetch(datetime(2020, 1, 1, tzinfo=timezone.utc)))
    # Items are deduplicated by link, so 2 items despite 2 feeds.
    assert len(items) == 2
```

- [ ] **Step 3: Verify failure**

```bash
pytest tests/unit/test_cssf_rss_source.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement `regwatch/pipeline/fetch/cssf_rss.py`**

```python
"""CSSF RSS source plugin: one feed per keyword, deduped by link."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from dateutil import parser as dateparser

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source


@register_source
class CssfRssSource:
    name = "cssf_rss"
    base_url = "https://www.cssf.lu/en/feed/publications"

    def __init__(self, keywords: list[str]) -> None:
        self.keywords = keywords
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        seen_links: set[str] = set()
        now = datetime.now(timezone.utc)
        for keyword in self.keywords:
            url = f"{self.base_url}?content_keyword={keyword}"
            response = self._client.get(url)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            for entry in feed.entries:
                link = getattr(entry, "link", None)
                if not link or link in seen_links:
                    continue
                published_at = _parse_date(entry)
                if published_at < since:
                    continue
                seen_links.add(link)
                yield RawDocument(
                    source=self.name,
                    source_url=link,
                    title=getattr(entry, "title", "").strip(),
                    published_at=published_at,
                    raw_payload=_entry_to_dict(entry, keyword),
                    fetched_at=now,
                )


def _parse_date(entry: Any) -> datetime:
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if raw is None:
        return datetime.now(timezone.utc)
    parsed = dateparser.parse(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _entry_to_dict(entry: Any, keyword: str) -> dict[str, Any]:
    return {
        "guid": getattr(entry, "id", None) or getattr(entry, "guid", None),
        "description": getattr(entry, "description", None),
        "keyword": keyword,
    }
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_cssf_rss_source.py -v
```
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/fetch/cssf_rss.py tests/fixtures/cssf_rss_sample.xml tests/unit/test_cssf_rss_source.py
git commit -m "feat(pipeline): add CSSF RSS source with fixture tests"
```

### Task 12: HTML extractor (trafilatura)

**Files:**
- Create: `regwatch/pipeline/extract/__init__.py`
- Create: `regwatch/pipeline/extract/html.py`
- Create: `tests/fixtures/cssf_circular_page.html`
- Create: `tests/unit/test_html_extractor.py`

- [ ] **Step 1: Create the HTML fixture** `tests/fixtures/cssf_circular_page.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <title>Circular CSSF 18/698 | CSSF</title>
  <meta charset="utf-8">
</head>
<body>
  <nav>navigation should be stripped</nav>
  <header>header should be stripped</header>
  <main>
    <article>
      <h1>Circular CSSF 18/698</h1>
      <p>This circular governs the authorisation and organisation of Investment Fund Managers.</p>
      <h2>Chapter 1: Scope</h2>
      <p>This chapter applies to all authorised AIFMs and Chapter 15 Management Companies.</p>
    </article>
  </main>
  <footer>footer should be stripped</footer>
</body>
</html>
```

- [ ] **Step 2: Write the failing test**

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from regwatch.domain.types import RawDocument
from regwatch.pipeline.extract.html import extract_html

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cssf_circular_page.html"


def _raw(url: str) -> RawDocument:
    now = datetime.now(timezone.utc)
    return RawDocument(
        source="cssf_rss",
        source_url=url,
        title="Circular CSSF 18/698",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )


def test_extract_html_strips_boilerplate(httpx_mock: HTTPXMock) -> None:
    url = "https://www.cssf.lu/en/Document/circular-cssf-18-698/"
    httpx_mock.add_response(url=url, content=FIXTURE.read_bytes(),
                            headers={"content-type": "text/html"})

    text = extract_html(_raw(url))

    assert text is not None
    assert "Investment Fund Managers" in text
    assert "navigation should be stripped" not in text
    assert "footer should be stripped" not in text


def test_extract_html_returns_none_for_pdf_link(httpx_mock: HTTPXMock) -> None:
    url = "https://www.cssf.lu/wp-content/uploads/cssf-25-901.pdf"
    # Don't register any mock — function should short-circuit on .pdf suffix.
    text = extract_html(_raw(url))
    assert text is None
```

- [ ] **Step 3: Verify failure**

```bash
pytest tests/unit/test_html_extractor.py -v
```

- [ ] **Step 4: Implement `regwatch/pipeline/extract/__init__.py`** (blank) and `regwatch/pipeline/extract/html.py`:

```python
"""HTML text extraction using trafilatura."""
from __future__ import annotations

import httpx
import trafilatura

from regwatch.domain.types import RawDocument

_HTTP_TIMEOUT = 30.0


def extract_html(raw: RawDocument) -> str | None:
    """Download the source URL, extract main text. Returns None for PDF URLs."""
    url = raw.source_url
    if url.lower().endswith(".pdf"):
        return None

    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "pdf" in content_type.lower():
            return None
        html = response.text

    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    return text
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_html_extractor.py -v
```
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add regwatch/pipeline/extract tests/fixtures/cssf_circular_page.html tests/unit/test_html_extractor.py
git commit -m "feat(pipeline): add HTML extractor using trafilatura"
```

### Task 13: PDF extractor with protection detection

**Files:**
- Create: `regwatch/pipeline/extract/pdf.py`
- Create: `tests/fixtures/sample_unprotected.pdf` (generated programmatically in the test if needed, or committed as a tiny fixture)
- Create: `tests/unit/test_pdf_extractor.py`

- [ ] **Step 1: Write the failing test**

```python
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import NameObject, createStringObject

from regwatch.domain.types import RawDocument
from regwatch.pipeline.extract.pdf import PdfExtractionResult, extract_pdf


def _raw_with_url(url: str) -> RawDocument:
    now = datetime.now(timezone.utc)
    return RawDocument(
        source="cssf_rss",
        source_url=url,
        title="Test PDF",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )


def _make_unprotected_pdf(path: Path, text: str) -> None:
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, text)
    c.save()


def _make_protected_pdf(path: Path, tmp_path: Path) -> None:
    src = tmp_path / "src.pdf"
    _make_unprotected_pdf(src, "secret content")
    writer = PdfWriter(clone_from=str(src))
    writer.encrypt(user_password="user", owner_password="owner")
    with open(path, "wb") as f:
        writer.write(f)


def test_extract_unprotected_pdf(tmp_path: Path, httpx_mock) -> None:
    pytest.importorskip("reportlab")
    pdf_file = tmp_path / "doc.pdf"
    _make_unprotected_pdf(pdf_file, "Article 24 of AIFMD applies.")

    httpx_mock.add_response(
        url="https://example.com/doc.pdf",
        content=pdf_file.read_bytes(),
        headers={"content-type": "application/pdf"},
    )

    archive_root = tmp_path / "archive"
    result = extract_pdf(_raw_with_url("https://example.com/doc.pdf"), archive_root)

    assert isinstance(result, PdfExtractionResult)
    assert result.is_protected is False
    assert result.text is not None
    assert "Article 24" in result.text
    assert Path(result.archive_path).exists()


def test_extract_protected_pdf_sets_flag(tmp_path: Path, httpx_mock) -> None:
    pytest.importorskip("reportlab")
    src = tmp_path / "protected.pdf"
    _make_protected_pdf(src, tmp_path)

    httpx_mock.add_response(
        url="https://example.com/locked.pdf",
        content=src.read_bytes(),
        headers={"content-type": "application/pdf"},
    )

    archive_root = tmp_path / "archive"
    result = extract_pdf(_raw_with_url("https://example.com/locked.pdf"), archive_root)

    assert result.is_protected is True
    assert result.text is None
    assert Path(result.archive_path).exists()
```

**Note:** `reportlab` is only used for test PDF generation and is added to the dev extras. Add `reportlab>=4.1` to `[project.optional-dependencies].dev` in `pyproject.toml` in this task.

- [ ] **Step 2: Add reportlab dev dependency**

In `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "pytest-httpx>=0.30",
  "ruff>=0.4",
  "mypy>=1.10",
  "freezegun>=1.5",
  "reportlab>=4.1",
]
```

Then run `pip install -e .[dev]` to pick up the new dep.

- [ ] **Step 3: Verify failure**

```bash
pytest tests/unit/test_pdf_extractor.py -v
```

- [ ] **Step 4: Implement `regwatch/pipeline/extract/pdf.py`**

```python
"""PDF download, archive, text extraction, and protection detection."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import pdfplumber
import pypdf
from slugify import slugify

from regwatch.domain.types import RawDocument


@dataclass
class PdfExtractionResult:
    archive_path: str
    text: str | None
    is_protected: bool


def extract_pdf(raw: RawDocument, archive_root: Path | str) -> PdfExtractionResult:
    """Download the PDF, archive it under `archive_root`, and extract text if possible."""
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        response = client.get(raw.source_url)
        response.raise_for_status()
        data = response.content

    sha = hashlib.sha256(data).hexdigest()
    when = raw.published_at
    subdir = Path(archive_root) / f"{when.year:04d}" / f"{when.month:02d}"
    subdir.mkdir(parents=True, exist_ok=True)
    slug = slugify(raw.title or "document", max_length=60)
    archive_path = subdir / f"{sha[:8]}-{slug}.pdf"
    archive_path.write_bytes(data)

    text, is_protected = _extract_text(archive_path)
    return PdfExtractionResult(
        archive_path=str(archive_path), text=text, is_protected=is_protected
    )


def _extract_text(pdf_path: Path) -> tuple[str | None, bool]:
    """Return (text, is_protected). text is None iff extraction failed."""
    # Pass 1: pdfplumber (most robust for layout).
    try:
        with pdfplumber.open(pdf_path) as pdf:
            parts = [page.extract_text() or "" for page in pdf.pages]
            joined = "\n".join(p for p in parts if p).strip()
            if joined:
                return joined, False
    except Exception:  # noqa: BLE001 — we fall through to pypdf
        pass

    # Pass 2: pypdf. Detect protection explicitly.
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                return None, True
            if reader.is_encrypted:
                return None, True
        parts = [(page.extract_text() or "") for page in reader.pages]
        joined = "\n".join(p for p in parts if p).strip()
        if joined:
            return joined, False
        # Empty text from an unencrypted PDF is a real failure, not protection.
        return None, False
    except pypdf.errors.PdfReadError:
        return None, True
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_pdf_extractor.py -v
```
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml regwatch/pipeline/extract/pdf.py tests/unit/test_pdf_extractor.py
git commit -m "feat(pipeline): add PDF extractor with protection detection"
```

### Task 14: Rule-based matcher (regex + CELEX + ELI)

**Files:**
- Create: `regwatch/pipeline/match/__init__.py`
- Create: `regwatch/pipeline/match/rules.py`
- Create: `tests/unit/test_rules_matcher.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationAlias,
    RegulationType,
)
from regwatch.pipeline.match.rules import RuleMatcher


def _make_session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_regulation(
    session: Session,
    reference: str,
    *,
    celex: str | None = None,
    aliases: list[tuple[str, str]] | None = None,
) -> int:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR if not celex else RegulationType.EU_REGULATION,
        reference_number=reference,
        celex_id=celex,
        title=reference,
        issuing_authority="CSSF" if not celex else "EU",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()
    for pattern, kind in aliases or []:
        session.add(RegulationAlias(regulation_id=reg.regulation_id, pattern=pattern, kind=kind))
    session.flush()
    return reg.regulation_id


def test_matches_exact_circular_reference(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    rid = _add_regulation(
        session,
        "CSSF 18/698",
        aliases=[(r"CSSF[\s\-]?18[/\-]698", "REGEX"), ("Circular 18/698", "EXACT")],
    )

    matcher = RuleMatcher(session)
    text = "This note references Circular 18/698 and also CSSF 18-698 in another place."
    matches = matcher.match(text)

    assert len(matches) >= 1
    assert all(m.regulation_id == rid for m in matches)
    assert any(m.method == "REGEX_ALIAS" for m in matches)


def test_matches_celex_id(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    rid = _add_regulation(session, "DORA", celex="32022R2554")

    matcher = RuleMatcher(session)
    text = "As required by Regulation (EU) 2022/2554, also known by CELEX 32022R2554 ..."
    matches = matcher.match(text)

    assert any(m.regulation_id == rid and m.method == "CELEX_ID" for m in matches)


def test_matches_eli_uri(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    reg = Regulation(
        type=RegulationType.LU_LAW,
        reference_number="Law of 12 July 2013",
        eli_uri="http://data.legilux.public.lu/eli/etat/leg/loi/2013/07/12/n6/jo",
        title="AIFM Law",
        issuing_authority="Luxembourg",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.commit()

    matcher = RuleMatcher(session)
    text = "The applicable law is at http://data.legilux.public.lu/eli/etat/leg/loi/2013/07/12/n6/jo"
    matches = matcher.match(text)

    assert any(m.regulation_id == reg.regulation_id and m.method == "ELI_URI" for m in matches)


def test_no_match_returns_empty_list(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    _add_regulation(session, "CSSF 18/698", aliases=[("CSSF 18/698", "EXACT")])

    matcher = RuleMatcher(session)
    assert matcher.match("This text mentions nothing regulatory.") == []
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/unit/test_rules_matcher.py -v
```

- [ ] **Step 3: Implement `regwatch/pipeline/match/__init__.py`** (blank) and `regwatch/pipeline/match/rules.py`:

```python
"""Rule-based matcher: regex aliases, CELEX IDs, and ELI URIs."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from regwatch.db.models import Regulation, RegulationAlias
from regwatch.domain.types import MatchedReference

CELEX_PATTERN = re.compile(r"\b[1-9]\d{4}[A-Z]\d{4}\b")
ELI_PATTERN = re.compile(
    r"https?://data\.(?:europa\.eu|legilux\.public\.lu)/eli/[^\s)\]]+",
    re.IGNORECASE,
)


class RuleMatcher:
    def __init__(self, session: Session) -> None:
        self._session = session

    def match(self, text: str) -> list[MatchedReference]:
        if not text:
            return []

        results: list[MatchedReference] = []
        seen_keys: set[tuple[int, str]] = set()

        # 1. Regex / exact aliases.
        for alias, regulation_id in self._load_aliases():
            if alias.kind == "REGEX":
                pattern = re.compile(alias.pattern, re.IGNORECASE)
            elif alias.kind == "EXACT":
                pattern = re.compile(re.escape(alias.pattern), re.IGNORECASE)
            else:
                continue
            match = pattern.search(text)
            if match is not None:
                key = (regulation_id, "REGEX_ALIAS")
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(
                        MatchedReference(
                            regulation_id=regulation_id,
                            method="REGEX_ALIAS",
                            confidence=1.0,
                            snippet=_snippet(text, match.start(), match.end()),
                        )
                    )

        # 2. CELEX IDs.
        for celex_match in CELEX_PATTERN.finditer(text):
            celex = celex_match.group(0)
            rid = self._regulation_id_by_celex(celex)
            if rid is None:
                continue
            key = (rid, "CELEX_ID")
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(
                    MatchedReference(
                        regulation_id=rid,
                        method="CELEX_ID",
                        confidence=1.0,
                        snippet=_snippet(text, celex_match.start(), celex_match.end()),
                    )
                )

        # 3. ELI URIs.
        for eli_match in ELI_PATTERN.finditer(text):
            eli = eli_match.group(0).rstrip(".,;)")
            rid = self._regulation_id_by_eli(eli)
            if rid is None:
                continue
            key = (rid, "ELI_URI")
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(
                    MatchedReference(
                        regulation_id=rid,
                        method="ELI_URI",
                        confidence=1.0,
                        snippet=_snippet(text, eli_match.start(), eli_match.end()),
                    )
                )

        return results

    def _load_aliases(self) -> list[tuple[RegulationAlias, int]]:
        rows = (
            self._session.query(RegulationAlias, RegulationAlias.regulation_id).all()
        )
        return [(alias, rid) for alias, rid in rows]

    def _regulation_id_by_celex(self, celex: str) -> int | None:
        row = (
            self._session.query(Regulation.regulation_id)
            .filter(Regulation.celex_id == celex)
            .one_or_none()
        )
        return row[0] if row is not None else None

    def _regulation_id_by_eli(self, eli: str) -> int | None:
        row = (
            self._session.query(Regulation.regulation_id)
            .filter(Regulation.eli_uri == eli)
            .one_or_none()
        )
        return row[0] if row is not None else None


def _snippet(text: str, start: int, end: int, radius: int = 60) -> str:
    s = max(0, start - radius)
    e = min(len(text), end + radius)
    return text[s:e].strip()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_rules_matcher.py -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/pipeline/match tests/unit/test_rules_matcher.py
git commit -m "feat(pipeline): add rule-based matcher for aliases, CELEX and ELI"
```

### Task 15: Lifecycle classifier (rule-based portion)

**Files:**
- Create: `regwatch/pipeline/match/lifecycle.py`
- Create: `tests/unit/test_lifecycle_classifier.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date

from regwatch.pipeline.match.lifecycle import classify_lifecycle


def test_celex_proposal_prefix() -> None:
    assert classify_lifecycle(
        title="Proposal for a Directive amending AIFMD",
        celex_id="52021PC0721",
        url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:52021PC0721",
        application_date=None,
        today=date(2026, 4, 8),
    ) == "PROPOSAL"


def test_celex_adopted_in_force() -> None:
    assert classify_lifecycle(
        title="Directive 2022/2554 DORA",
        celex_id="32022R2554",
        url="https://example.com",
        application_date=date(2025, 1, 17),
        today=date(2026, 4, 8),
    ) == "IN_FORCE"


def test_celex_adopted_not_in_force() -> None:
    assert classify_lifecycle(
        title="Directive 2024/927 AIFMD II",
        celex_id="32024L0927",
        url="https://example.com",
        application_date=date(2027, 4, 16),
        today=date(2026, 4, 8),
    ) == "ADOPTED_NOT_IN_FORCE"


def test_legilux_draft_bill_uri() -> None:
    assert classify_lifecycle(
        title="Projet de loi 8628",
        celex_id=None,
        url="http://data.legilux.public.lu/eli/etat/projet-de-loi/2025/10/08/a1/jo",
        application_date=None,
        today=date(2026, 4, 8),
    ) == "DRAFT_BILL"


def test_title_heuristic_consultation() -> None:
    assert classify_lifecycle(
        title="Consultation paper on liquidity management tools",
        celex_id=None,
        url="https://www.esma.europa.eu/consultation",
        application_date=None,
        today=date(2026, 4, 8),
    ) == "CONSULTATION"


def test_default_is_in_force() -> None:
    assert classify_lifecycle(
        title="Circular CSSF 25/901 on outsourcing",
        celex_id=None,
        url="https://www.cssf.lu/en/Document/circular-cssf-25-901/",
        application_date=None,
        today=date(2026, 4, 8),
    ) == "IN_FORCE"
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/unit/test_lifecycle_classifier.py -v
```

- [ ] **Step 3: Implement `regwatch/pipeline/match/lifecycle.py`**

```python
"""Rule-based lifecycle classifier (runs before any Ollama backup)."""
from __future__ import annotations

import re
from datetime import date

CELEX_PROPOSAL = re.compile(r"^5\d{4}P[CP]\d{4}$")
CELEX_ADOPTED = re.compile(r"^3\d{4}[A-Z]\d{4}$")

LEGILUX_DRAFT_BILL = re.compile(
    r"data\.legilux\.public\.lu/eli/etat/projet-de-loi/",
    re.IGNORECASE,
)

CONSULTATION_KEYWORDS = (
    "consultation paper",
    "discussion paper",
    "feedback on",
    "call for evidence",
)


def classify_lifecycle(
    *,
    title: str,
    celex_id: str | None,
    url: str,
    application_date: date | None,
    today: date,
) -> str:
    """Return a lifecycle_stage string based on deterministic rules.

    Rules apply in order. The first match wins. Returns "IN_FORCE" as default.
    """
    # Rule 1: CELEX proposal prefix.
    if celex_id and CELEX_PROPOSAL.match(celex_id):
        return "PROPOSAL"

    # Rule 2: CELEX adopted + application date.
    if celex_id and CELEX_ADOPTED.match(celex_id):
        if application_date and application_date > today:
            return "ADOPTED_NOT_IN_FORCE"
        return "IN_FORCE"

    # Rule 3: Legilux draft bill URI.
    if LEGILUX_DRAFT_BILL.search(url):
        return "DRAFT_BILL"

    # Rule 4: Title heuristics for consultations.
    title_lower = title.lower()
    if any(kw in title_lower for kw in CONSULTATION_KEYWORDS):
        return "CONSULTATION"

    # Default.
    return "IN_FORCE"
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_lifecycle_classifier.py -v
```
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/pipeline/match/lifecycle.py tests/unit/test_lifecycle_classifier.py
git commit -m "feat(pipeline): add rule-based lifecycle classifier"
```

### Task 16: ICT flag and severity heuristics

**Files:**
- Create: `regwatch/pipeline/match/classify.py`
- Create: `tests/unit/test_classify_heuristics.py`

- [ ] **Step 1: Write the failing test**

```python
from regwatch.pipeline.match.classify import is_ict_document, severity_for


def test_ict_keywords_trigger_flag() -> None:
    assert is_ict_document("DORA incident reporting requirements") is True
    assert is_ict_document("Third-party ICT risk management") is True
    assert is_ict_document("Cyber resilience testing rules") is True


def test_non_ict_documents() -> None:
    assert is_ict_document("Remuneration policies for UCITS") is False
    assert is_ict_document("NAV errors and breaches") is False


def test_severity_critical_for_amendment_with_ict() -> None:
    assert severity_for(
        title="Amending regulation on ICT risk management",
        is_ict=True,
        references_in_force=True,
    ) == "CRITICAL"


def test_severity_material_for_amendment_without_ict() -> None:
    assert severity_for(
        title="Amending regulation on remuneration",
        is_ict=False,
        references_in_force=True,
    ) == "MATERIAL"


def test_severity_informational_default() -> None:
    assert severity_for(
        title="FAQ update",
        is_ict=False,
        references_in_force=False,
    ) == "INFORMATIONAL"
```

- [ ] **Step 2: Verify failure, then implement `regwatch/pipeline/match/classify.py`**

```python
"""Keyword heuristics for `is_ict` and severity."""
from __future__ import annotations

_ICT_KEYWORDS = (
    "dora",
    "ict",
    "cyber",
    "operational resilience",
    "outsourcing",
    "tlpt",
    "third-party provider",
    "third party provider",
    "incident reporting",
    "digital operational resilience",
)


def is_ict_document(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _ICT_KEYWORDS)


_AMENDMENT_MARKERS = ("amend", "amending", "repeal", "replacing", "supersede")


def severity_for(*, title: str, is_ict: bool, references_in_force: bool) -> str:
    lower = title.lower()
    is_amendment = any(marker in lower for marker in _AMENDMENT_MARKERS)
    if is_amendment and references_in_force:
        return "CRITICAL" if is_ict else "MATERIAL"
    if is_amendment or references_in_force:
        return "MATERIAL"
    return "INFORMATIONAL"
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_classify_heuristics.py -v
git add regwatch/pipeline/match/classify.py tests/unit/test_classify_heuristics.py
git commit -m "feat(pipeline): add ICT and severity heuristics"
```

### Task 17: Diff generator

**Files:**
- Create: `regwatch/pipeline/diff.py`
- Create: `tests/unit/test_diff.py`

- [ ] **Step 1: Write the test and implement in one step (small module)**

`tests/unit/test_diff.py`:
```python
from regwatch.pipeline.diff import compute_diff


def test_returns_none_for_identical_texts() -> None:
    assert compute_diff("hello world", "hello world") is None


def test_generates_unified_diff_for_changes() -> None:
    old = "line one\nline two\nline three\n"
    new = "line one\nline two modified\nline three\n"
    result = compute_diff(old, new)
    assert result is not None
    assert "- line two" in result
    assert "+ line two modified" in result


def test_handles_added_and_removed_lines() -> None:
    old = "a\nb\nc\n"
    new = "a\nb\nc\nd\n"
    result = compute_diff(old, new)
    assert result is not None
    assert "+ d" in result
```

`regwatch/pipeline/diff.py`:
```python
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
```

- [ ] **Step 2: Run and commit**

```bash
pytest tests/unit/test_diff.py -v
git add regwatch/pipeline/diff.py tests/unit/test_diff.py
git commit -m "feat(pipeline): add unified diff generator"
```

### Task 18: Persist phase

**Files:**
- Create: `regwatch/pipeline/persist.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_persist.py`

- [ ] **Step 1: Write the failing test**

`tests/integration/test_persist.py`:
```python
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.domain.types import (
    ExtractedDocument,
    MatchedDocument,
    MatchedReference,
    RawDocument,
)
from regwatch.pipeline.persist import persist_matched


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_reg(session: Session, reference: str) -> int:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=reference,
        title=reference,
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()
    return reg.regulation_id


def _matched(text: str, *, references: list[int], url: str = "https://example.com/a") -> MatchedDocument:
    now = datetime.now(timezone.utc)
    raw = RawDocument(
        source="cssf_rss",
        source_url=url,
        title="Sample",
        published_at=now,
        raw_payload={},
        fetched_at=now,
    )
    ext = ExtractedDocument(
        raw=raw,
        html_text=text,
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )
    return MatchedDocument(
        extracted=ext,
        references=[
            MatchedReference(regulation_id=rid, method="REGEX_ALIAS", confidence=1.0)
            for rid in references
        ],
        lifecycle_stage="IN_FORCE",
        is_ict=False,
        severity="MATERIAL",
    )


def test_persist_creates_event_and_links(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rid = _add_reg(session, "CSSF 18/698")
    session.commit()

    result = persist_matched(session, _matched("text v1", references=[rid]))
    session.commit()

    assert result.events_created == 1
    assert result.versions_created == 1

    ev = session.query(UpdateEvent).one()
    assert ev.source == "cssf_rss"
    links = session.query(UpdateEventRegulationLink).all()
    assert len(links) == 1
    versions = session.query(DocumentVersion).all()
    assert len(versions) == 1
    assert versions[0].version_number == 1
    assert versions[0].is_current is True


def test_persist_is_idempotent(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rid = _add_reg(session, "CSSF 18/698")
    session.commit()

    m = _matched("text v1", references=[rid])
    persist_matched(session, m)
    persist_matched(session, m)
    session.commit()

    assert session.query(UpdateEvent).count() == 1
    assert session.query(DocumentVersion).count() == 1


def test_persist_creates_new_version_on_content_change(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rid = _add_reg(session, "CSSF 18/698")
    session.commit()

    persist_matched(session, _matched("original", references=[rid], url="https://example.com/v1"))
    session.commit()
    persist_matched(session, _matched("revised text", references=[rid], url="https://example.com/v2"))
    session.commit()

    versions = (
        session.query(DocumentVersion)
        .filter(DocumentVersion.regulation_id == rid)
        .order_by(DocumentVersion.version_number)
        .all()
    )
    assert len(versions) == 2
    assert versions[0].is_current is False
    assert versions[1].is_current is True
    assert versions[1].version_number == 2
    assert versions[1].change_summary is not None
    assert "- original" in versions[1].change_summary
    assert "+ revised text" in versions[1].change_summary
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/integration/test_persist.py -v
```

- [ ] **Step 3: Implement `regwatch/pipeline/persist.py`**

```python
"""Phase 4: persist the matched document into SQLite in a single transaction."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentVersion,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.domain.types import MatchedDocument
from regwatch.pipeline.diff import compute_diff


@dataclass
class PersistResult:
    event_id: int | None
    events_created: int
    versions_created: int


def persist_matched(session: Session, matched: MatchedDocument) -> PersistResult:
    """Insert the matched document and all related rows. Idempotent by content hash."""
    extracted = matched.extracted
    raw = extracted.raw

    text_for_hash = _text_for_hashing(extracted)
    content_hash = hashlib.sha256(text_for_hash.encode("utf-8")).hexdigest()

    # Idempotency: skip if we already have an event with this content hash.
    existing = session.scalar(
        select(UpdateEvent).where(UpdateEvent.content_hash == content_hash)
    )
    if existing is not None:
        return PersistResult(event_id=existing.event_id, events_created=0, versions_created=0)

    event = UpdateEvent(
        source=raw.source,
        source_url=raw.source_url,
        title=raw.title,
        published_at=raw.published_at,
        fetched_at=raw.fetched_at,
        raw_payload=raw.raw_payload,
        content_hash=content_hash,
        is_ict=matched.is_ict,
        severity=matched.severity,
        review_status="NEW",
    )
    for ref in matched.references:
        event.regulation_links.append(
            UpdateEventRegulationLink(
                regulation_id=ref.regulation_id,
                match_method=ref.method,
                confidence=ref.confidence,
                matched_snippet=ref.snippet,
            )
        )
    session.add(event)
    session.flush()

    versions_created = 0
    for ref in matched.references:
        if _create_new_version(session, ref.regulation_id, extracted, text_for_hash, content_hash):
            versions_created += 1

    return PersistResult(
        event_id=event.event_id, events_created=1, versions_created=versions_created
    )


def _text_for_hashing(extracted) -> str:
    return (extracted.pdf_extracted_text or extracted.html_text or "").strip()


def _create_new_version(
    session: Session,
    regulation_id: int,
    extracted,
    text: str,
    content_hash: str,
) -> bool:
    """Insert a new document_version row if content has changed. Returns True if inserted."""
    current = session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.regulation_id == regulation_id)
        .where(DocumentVersion.is_current == True)  # noqa: E712
    )
    if current is not None and current.content_hash == content_hash:
        return False

    prev_text = ""
    prev_number = 0
    if current is not None:
        prev_text = current.pdf_extracted_text or current.html_text or ""
        prev_number = current.version_number
        session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.version_id == current.version_id)
            .values(is_current=False)
        )

    change_summary = compute_diff(prev_text, text) if prev_text else None

    new_version = DocumentVersion(
        regulation_id=regulation_id,
        version_number=prev_number + 1,
        is_current=True,
        fetched_at=datetime.now(timezone.utc),
        source_url=extracted.raw.source_url,
        content_hash=content_hash,
        html_text=extracted.html_text,
        pdf_path=extracted.pdf_path,
        pdf_extracted_text=extracted.pdf_extracted_text,
        pdf_is_protected=extracted.pdf_is_protected,
        pdf_manual_upload=False,
        change_summary=change_summary,
    )
    session.add(new_version)
    session.flush()
    return True
```

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/integration/test_persist.py -v
git add regwatch/pipeline/persist.py tests/integration/__init__.py tests/integration/test_persist.py
git commit -m "feat(pipeline): add persist phase with idempotency and diffs"
```

### Task 19: Pipeline runner

**Files:**
- Create: `regwatch/pipeline/runner.py`
- Create: `tests/integration/test_runner.py`

- [ ] **Step 1: Write the failing test**

```python
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, PipelineRun, UpdateEvent
from regwatch.domain.types import RawDocument
from regwatch.pipeline.runner import PipelineRunner, SourceFailure


class _FakeSource:
    name = "fake_success"

    def __init__(self, docs: list[RawDocument]) -> None:
        self._docs = docs

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        yield from self._docs


class _FailingSource:
    name = "fake_failing"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        raise RuntimeError("boom")


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _raw(title: str, url: str, text: str) -> RawDocument:
    now = datetime.now(timezone.utc)
    return RawDocument(
        source="fake_success",
        source_url=url,
        title=title,
        published_at=now,
        raw_payload={"text": text},
        fetched_at=now,
    )


def test_runner_success_path(tmp_path: Path) -> None:
    session = _session(tmp_path)
    source = _FakeSource(
        [
            _raw("Doc A", "https://example.com/a", "Some content A"),
            _raw("Doc B", "https://example.com/b", "Some content B"),
        ]
    )

    runner = PipelineRunner(
        session,
        sources=[source],
        extract=lambda raw: _stub_extract(raw),
        match=lambda extracted: _stub_match(extracted),
    )
    run_id = runner.run_once()
    session.commit()

    pr = session.get(PipelineRun, run_id)
    assert pr is not None
    assert pr.status == "COMPLETED"
    assert pr.events_created == 2
    assert pr.sources_attempted == ["fake_success"]
    assert pr.sources_failed == []

    events = session.query(UpdateEvent).all()
    assert len(events) == 2


def test_runner_failing_source_does_not_block_others(tmp_path: Path) -> None:
    session = _session(tmp_path)
    good = _FakeSource([_raw("Doc A", "https://example.com/a", "content")])
    bad = _FailingSource()

    runner = PipelineRunner(
        session,
        sources=[bad, good],
        extract=lambda raw: _stub_extract(raw),
        match=lambda extracted: _stub_match(extracted),
    )
    run_id = runner.run_once()
    session.commit()

    pr = session.get(PipelineRun, run_id)
    assert pr is not None
    assert pr.status == "COMPLETED"
    assert "fake_failing" in pr.sources_failed
    assert "fake_success" in pr.sources_attempted
    assert pr.events_created == 1


def _stub_extract(raw: RawDocument):
    from regwatch.domain.types import ExtractedDocument

    return ExtractedDocument(
        raw=raw,
        html_text=raw.raw_payload.get("text"),
        pdf_path=None,
        pdf_extracted_text=None,
        pdf_is_protected=False,
    )


def _stub_match(extracted):
    from regwatch.domain.types import MatchedDocument

    return MatchedDocument(
        extracted=extracted,
        references=[],
        lifecycle_stage="IN_FORCE",
        is_ict=False,
        severity="INFORMATIONAL",
    )
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/integration/test_runner.py -v
```

- [ ] **Step 3: Implement `regwatch/pipeline/runner.py`**

```python
"""Pipeline runner: orchestrates Fetch → Extract → Match → Persist → Notify."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from regwatch.db.models import PipelineRun
from regwatch.domain.types import ExtractedDocument, MatchedDocument, RawDocument
from regwatch.pipeline.persist import persist_matched

logger = logging.getLogger(__name__)

ExtractFn = Callable[[RawDocument], ExtractedDocument]
MatchFn = Callable[[ExtractedDocument], MatchedDocument]


@dataclass
class SourceFailure:
    source_name: str
    error: str


class PipelineRunner:
    def __init__(
        self,
        session: Session,
        *,
        sources: Iterable,
        extract: ExtractFn,
        match: MatchFn,
    ) -> None:
        self._session = session
        self._sources = list(sources)
        self._extract = extract
        self._match = match

    def run_once(self, since: datetime | None = None) -> int:
        """Run all sources once. Returns the pipeline_run id."""
        self._abort_stale_runs()
        run = PipelineRun(
            started_at=datetime.now(timezone.utc),
            status="RUNNING",
            sources_attempted=[],
            sources_failed=[],
            events_created=0,
            versions_created=0,
        )
        self._session.add(run)
        self._session.flush()

        since = since or datetime(2000, 1, 1, tzinfo=timezone.utc)

        for source in self._sources:
            run.sources_attempted = [*run.sources_attempted, source.name]
            try:
                for raw in source.fetch(since):
                    try:
                        extracted = self._extract(raw)
                        matched = self._match(extracted)
                        result = persist_matched(self._session, matched)
                        run.events_created += result.events_created
                        run.versions_created += result.versions_created
                    except Exception:  # noqa: BLE001
                        logger.exception("Per-document failure in %s", source.name)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Source %s failed", source.name)
                run.sources_failed = [*run.sources_failed, source.name]

        run.finished_at = datetime.now(timezone.utc)
        run.status = "COMPLETED"
        self._session.flush()
        return run.run_id

    def _abort_stale_runs(self) -> None:
        self._session.execute(
            update(PipelineRun)
            .where(PipelineRun.status == "RUNNING")
            .values(status="ABORTED", finished_at=datetime.now(timezone.utc))
        )
```

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/integration/test_runner.py -v
git add regwatch/pipeline/runner.py tests/integration/test_runner.py
git commit -m "feat(pipeline): add pipeline runner with per-source isolation"
```

### Task 20: End-to-end pipeline wiring with real match functions

**Files:**
- Create: `regwatch/pipeline/pipeline_factory.py`
- Create: `tests/integration/test_pipeline_end_to_end.py`

This task provides a factory that wires the real extract and match phases together. The factory accepts a session and the app config, and returns a configured `PipelineRunner`.

- [ ] **Step 1: Write the failing test**

```python
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationAlias,
    RegulationType,
    UpdateEvent,
    UpdateEventRegulationLink,
)
from regwatch.domain.types import RawDocument
from regwatch.pipeline.pipeline_factory import build_runner


class _FakeSource:
    name = "fake_end_to_end"

    def __init__(self, docs: list[RawDocument]) -> None:
        self._docs = docs

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        yield from self._docs


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_end_to_end_rule_match_without_ollama(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 18/698",
        title="IFM",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    reg.aliases.append(RegulationAlias(pattern=r"CSSF[\s\-]?18[/\-]698", kind="REGEX"))
    session.add(reg)
    session.commit()

    now = datetime.now(timezone.utc)
    raw = RawDocument(
        source="fake_end_to_end",
        source_url="https://example.com/x",
        title="New note referencing CSSF 18/698",
        published_at=now,
        raw_payload={"html_text": "This note refers to CSSF 18/698 and nothing else."},
        fetched_at=now,
    )

    runner = build_runner(
        session,
        sources=[_FakeSource([raw])],
        archive_root=tmp_path / "pdfs",
        ollama_enabled=False,
    )
    runner.run_once()
    session.commit()

    events = session.query(UpdateEvent).all()
    assert len(events) == 1
    links = session.query(UpdateEventRegulationLink).all()
    assert len(links) == 1
    assert links[0].regulation_id == reg.regulation_id
    assert links[0].match_method == "REGEX_ALIAS"
```

- [ ] **Step 2: Verify failure**

```bash
pytest tests/integration/test_pipeline_end_to_end.py -v
```

- [ ] **Step 3: Implement `regwatch/pipeline/pipeline_factory.py`**

```python
"""Factory that wires sources, extract and match functions into a PipelineRunner.

For tests that do not have network or real URLs, the raw_payload may carry a
pre-extracted `html_text` key. The factory's extract function honours that first.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from regwatch.domain.types import ExtractedDocument, MatchedDocument, RawDocument
from regwatch.pipeline.extract.html import extract_html
from regwatch.pipeline.extract.pdf import extract_pdf
from regwatch.pipeline.match.classify import is_ict_document, severity_for
from regwatch.pipeline.match.lifecycle import classify_lifecycle
from regwatch.pipeline.match.rules import RuleMatcher
from regwatch.pipeline.runner import PipelineRunner


def build_runner(
    session: Session,
    *,
    sources: Iterable,
    archive_root: Path | str,
    ollama_enabled: bool = False,
) -> PipelineRunner:
    rule_matcher = RuleMatcher(session)

    def _extract(raw: RawDocument) -> ExtractedDocument:
        # Test hook: prefer in-memory text over real HTTP.
        prefetched = raw.raw_payload.get("html_text") if raw.raw_payload else None
        if prefetched:
            return ExtractedDocument(
                raw=raw,
                html_text=prefetched,
                pdf_path=None,
                pdf_extracted_text=None,
                pdf_is_protected=False,
            )
        if raw.source_url.lower().endswith(".pdf"):
            result = extract_pdf(raw, archive_root)
            return ExtractedDocument(
                raw=raw,
                html_text=None,
                pdf_path=result.archive_path,
                pdf_extracted_text=result.text,
                pdf_is_protected=result.is_protected,
            )
        text = extract_html(raw)
        return ExtractedDocument(
            raw=raw,
            html_text=text,
            pdf_path=None,
            pdf_extracted_text=None,
            pdf_is_protected=False,
        )

    def _match(extracted: ExtractedDocument) -> MatchedDocument:
        text_for_match = (
            extracted.pdf_extracted_text or extracted.html_text or extracted.raw.title or ""
        )
        references = rule_matcher.match(text_for_match)
        is_ict = is_ict_document(extracted.raw.title + " " + (text_for_match or ""))
        lifecycle = classify_lifecycle(
            title=extracted.raw.title,
            celex_id=None,
            url=extracted.raw.source_url,
            application_date=None,
            today=date.today(),
        )
        severity = severity_for(
            title=extracted.raw.title,
            is_ict=is_ict,
            references_in_force=bool(references),
        )
        return MatchedDocument(
            extracted=extracted,
            references=references,
            lifecycle_stage=lifecycle,
            is_ict=is_ict,
            severity=severity,
        )

    return PipelineRunner(session, sources=sources, extract=_extract, match=_match)
```

- [ ] **Step 4: Run tests and commit**

```bash
pytest tests/integration/test_pipeline_end_to_end.py -v
git add regwatch/pipeline/pipeline_factory.py tests/integration/test_pipeline_end_to_end.py
git commit -m "feat(pipeline): add end-to-end pipeline factory"
```

---

## Phase 3 — Remaining sources

Each source in this phase follows the same structural pattern as Task 11 (CSSF RSS): a fixture file containing a recorded response, a test that asserts parsing + filtering behaviour, and an implementation that uses `httpx` for HTTP and either `feedparser` (RSS) or `SPARQLWrapper` (SPARQL). Each task must:

1. Commit a recorded fixture in `tests/fixtures/` named after the source.
2. Add tests in `tests/unit/test_<source_name>.py`.
3. Implement the source class in `regwatch/pipeline/fetch/<source_name>.py` with `@register_source`.
4. Run the test and commit with `feat(pipeline): add <source name> source`.

### Task 21: EUR-Lex SPARQL (adopted + proposals)

**Files:**
- Create: `regwatch/pipeline/fetch/eur_lex_adopted.py`
- Create: `regwatch/pipeline/fetch/eur_lex_proposal.py`
- Create: `tests/fixtures/eur_lex_adopted_sample.json`
- Create: `tests/fixtures/eur_lex_proposal_sample.json`
- Create: `tests/unit/test_eur_lex_adopted_source.py`
- Create: `tests/unit/test_eur_lex_proposal_source.py`

- [ ] **Step 1: Create `tests/fixtures/eur_lex_adopted_sample.json`** — a recorded SPARQL response in JSON format. Example (trim or extend as needed):

```json
{
  "head": {"vars": ["work", "celex", "title", "date"]},
  "results": {
    "bindings": [
      {
        "work": {"type": "uri", "value": "http://publications.europa.eu/resource/celex/32024L0927"},
        "celex": {"type": "literal", "value": "32024L0927"},
        "title": {"type": "literal", "value": "Directive (EU) 2024/927 amending AIFMD"},
        "date": {"type": "literal", "value": "2024-03-26"}
      },
      {
        "work": {"type": "uri", "value": "http://publications.europa.eu/resource/celex/32022R2554"},
        "celex": {"type": "literal", "value": "32022R2554"},
        "title": {"type": "literal", "value": "Regulation (EU) 2022/2554 DORA"},
        "date": {"type": "literal", "value": "2022-12-14"}
      }
    ]
  }
}
```

- [ ] **Step 2: Write the test**

`tests/unit/test_eur_lex_adopted_source.py`:
```python
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from regwatch.pipeline.fetch.eur_lex_adopted import EurLexAdoptedSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "eur_lex_adopted_sample.json"


def test_fetch_parses_sparql_results() -> None:
    import json
    fixture_data = json.loads(FIXTURE.read_text())

    with patch.object(EurLexAdoptedSource, "_run_query", return_value=fixture_data):
        source = EurLexAdoptedSource(
            celex_prefixes=["32024L0927", "32022R2554"],
        )
        items = list(source.fetch(datetime(2000, 1, 1, tzinfo=timezone.utc)))

    assert len(items) == 2
    assert items[0].source == "eur_lex_adopted"
    assert "32024L0927" in items[0].raw_payload.get("celex", "")
    assert items[0].title.startswith("Directive")
```

- [ ] **Step 3: Implement `regwatch/pipeline/fetch/eur_lex_adopted.py`**

```python
"""EUR-Lex adopted acts via the CELLAR SPARQL endpoint."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from SPARQLWrapper import JSON, SPARQLWrapper

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source

ENDPOINT = "http://publications.europa.eu/webapi/rdf/sparql"


@register_source
class EurLexAdoptedSource:
    name = "eur_lex_adopted"

    def __init__(self, celex_prefixes: list[str]) -> None:
        self._celex_prefixes = celex_prefixes

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        results = self._run_query(self._build_query(since))
        now = datetime.now(timezone.utc)
        for binding in results.get("results", {}).get("bindings", []):
            celex = binding.get("celex", {}).get("value")
            title = binding.get("title", {}).get("value", "")
            date_str = binding.get("date", {}).get("value", "")
            work_uri = binding.get("work", {}).get("value", "")
            published_at = _parse_date(date_str)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}",
                title=title,
                published_at=published_at,
                raw_payload={"celex": celex, "work_uri": work_uri, "date": date_str},
                fetched_at=now,
            )

    def _build_query(self, since: datetime) -> str:
        filter_clause = " || ".join(
            f'STR(?celex) = "{prefix}"' for prefix in self._celex_prefixes
        ) or "true"
        since_iso = since.date().isoformat()
        return f"""
        PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
        SELECT ?work ?celex ?title ?date
        WHERE {{
          ?work cdm:resource_legal_id_celex ?celex .
          ?work cdm:work_date_document ?date .
          ?expression cdm:expression_belongs_to_work ?work ;
                      cdm:expression_title ?title ;
                      cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> .
          FILTER ({filter_clause})
          FILTER (?date >= "{since_iso}"^^xsd:date)
        }}
        ORDER BY DESC(?date)
        LIMIT 500
        """

    def _run_query(self, query: str) -> dict[str, Any]:
        wrapper = SPARQLWrapper(ENDPOINT)
        wrapper.setQuery(query)
        wrapper.setReturnFormat(JSON)
        return wrapper.queryAndConvert()  # type: ignore[return-value]


def _parse_date(s: str) -> datetime:
    # SPARQL returns dates as YYYY-MM-DD.
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
```

- [ ] **Step 4: Repeat for the proposal source**

Create `tests/fixtures/eur_lex_proposal_sample.json` with similar structure but `celex` prefixed `52021PC0721`.

Create `regwatch/pipeline/fetch/eur_lex_proposal.py`:

```python
"""EUR-Lex proposals (CELEX prefix 5*) via the CELLAR SPARQL endpoint."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from SPARQLWrapper import JSON, SPARQLWrapper

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source

ENDPOINT = "http://publications.europa.eu/webapi/rdf/sparql"


@register_source
class EurLexProposalSource:
    name = "eur_lex_proposal"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        results = self._run_query(self._build_query(since))
        now = datetime.now(timezone.utc)
        for binding in results.get("results", {}).get("bindings", []):
            celex = binding.get("celex", {}).get("value", "")
            if not celex.startswith("5"):
                continue
            title = binding.get("title", {}).get("value", "")
            date_str = binding.get("date", {}).get("value", "")
            published_at = _parse_date(date_str)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}",
                title=title,
                published_at=published_at,
                raw_payload={"celex": celex, "date": date_str},
                fetched_at=now,
            )

    def _build_query(self, since: datetime) -> str:
        since_iso = since.date().isoformat()
        return f"""
        PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
        SELECT ?work ?celex ?title ?date
        WHERE {{
          ?work cdm:resource_legal_id_celex ?celex .
          ?work cdm:work_date_document ?date .
          ?expression cdm:expression_belongs_to_work ?work ;
                      cdm:expression_title ?title ;
                      cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG> .
          FILTER (STRSTARTS(STR(?celex), "5"))
          FILTER (?date >= "{since_iso}"^^xsd:date)
        }}
        ORDER BY DESC(?date)
        LIMIT 500
        """

    def _run_query(self, query: str) -> dict[str, Any]:
        wrapper = SPARQLWrapper(ENDPOINT)
        wrapper.setQuery(query)
        wrapper.setReturnFormat(JSON)
        return wrapper.queryAndConvert()  # type: ignore[return-value]


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
```

- [ ] **Step 5: Run tests and commit**

```bash
pytest tests/unit/test_eur_lex_adopted_source.py tests/unit/test_eur_lex_proposal_source.py -v
git add regwatch/pipeline/fetch/eur_lex_adopted.py regwatch/pipeline/fetch/eur_lex_proposal.py \
        tests/fixtures/eur_lex_adopted_sample.json tests/fixtures/eur_lex_proposal_sample.json \
        tests/unit/test_eur_lex_adopted_source.py tests/unit/test_eur_lex_proposal_source.py
git commit -m "feat(pipeline): add EUR-Lex SPARQL sources (adopted + proposals)"
```

### Task 22: Legilux SPARQL sources (Mémorial A + parliamentary dossiers)

Follow the same pattern as Task 21. Endpoint: `http://data.legilux.public.lu/sparql`.

**Files:**
- Create: `regwatch/pipeline/fetch/legilux_sparql.py` (class name `LegiluxSparqlSource`, `name = "legilux_sparql"`).
- Create: `regwatch/pipeline/fetch/legilux_parliamentary.py` (class name `LegiluxParliamentarySource`, `name = "legilux_parliamentary"`).
- Create: `tests/fixtures/legilux_sparql_sample.json` — recorded response with a Mémorial A entry for a financial-sector law.
- Create: `tests/fixtures/legilux_parliamentary_sample.json` — recorded response with a `projet-de-loi` dossier (e.g. Draft Bill 8628).
- Create: `tests/unit/test_legilux_sparql_source.py`
- Create: `tests/unit/test_legilux_parliamentary_source.py`

**Open question flagged in spec:** The parliamentary dossier SPARQL shape may not exist for Legilux. If running the implementation reveals that no SPARQL data is available, fall back to HTML scraping of the dossier listing page `https://wdocs-pub.chd.lu/docs/exped/...`. In that case, document the fallback in a module docstring and raise a warning in the CLI logs.

Test pattern mirrors Task 21 exactly: patch `_run_query` to return fixture data, assert that items have correct `source`, `source_url`, and `title`.

**SPARQL query for Mémorial A (implementation skeleton):**

```python
class LegiluxSparqlSource:
    name = "legilux_sparql"

    def _build_query(self, since):
        since_iso = since.date().isoformat()
        return f"""
        PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
        SELECT ?work ?title ?date ?eli WHERE {{
          ?work a jolux:Act ;
                jolux:dateDocument ?date ;
                jolux:title ?title ;
                jolux:isRealizedBy ?expression .
          ?expression jolux:language <http://publications.europa.eu/resource/authority/language/ENG> .
          ?work jolux:eliUri ?eli .
          FILTER (?date >= "{since_iso}"^^xsd:date)
          FILTER (CONTAINS(LCASE(?title), "financial") || CONTAINS(LCASE(?title), "cssf"))
        }}
        ORDER BY DESC(?date)
        LIMIT 200
        """
```

**SPARQL query for parliamentary dossiers:** run against the `parliamentary dossiers` graph. If `SELECT ... WHERE { ?dossier a jolux:Draft ; ...}` does not work, fall back to scraping as noted.

Commit after both sources pass:
```bash
git add regwatch/pipeline/fetch/legilux_sparql.py regwatch/pipeline/fetch/legilux_parliamentary.py \
        tests/fixtures/legilux_sparql_sample.json tests/fixtures/legilux_parliamentary_sample.json \
        tests/unit/test_legilux_sparql_source.py tests/unit/test_legilux_parliamentary_source.py
git commit -m "feat(pipeline): add Legilux SPARQL sources (Mémorial A + parliamentary)"
```

### Task 23: ESMA, EBA and EC-FISMA RSS sources

All three follow the CSSF RSS pattern (Task 11). They use `feedparser` and `httpx`.

**Files:**
- Create: `regwatch/pipeline/fetch/esma_rss.py` (`name = "esma_rss"`, URL `https://www.esma.europa.eu/rss.xml`)
- Create: `regwatch/pipeline/fetch/eba_rss.py` (`name = "eba_rss"`, URL `https://www.eba.europa.eu/news-press/news/rss.xml`)
- Create: `regwatch/pipeline/fetch/ec_fisma_rss.py` (`name = "ec_fisma_rss"`, multiple URLs based on `item_type_id` and `topic_id`)
- Create: `tests/fixtures/esma_rss_sample.xml`
- Create: `tests/fixtures/eba_rss_sample.xml`
- Create: `tests/fixtures/ec_fisma_rss_sample.xml`
- Create: `tests/unit/test_esma_rss_source.py`
- Create: `tests/unit/test_eba_rss_source.py`
- Create: `tests/unit/test_ec_fisma_rss_source.py`

Implementation template (ESMA — the others differ only in URL and constructor args):

```python
"""ESMA news RSS source."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import feedparser
import httpx
from dateutil import parser as dateparser

from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import register_source


@register_source
class EsmaRssSource:
    name = "esma_rss"
    url = "https://www.esma.europa.eu/rss.xml"

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        now = datetime.now(timezone.utc)
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(self.url)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        for entry in feed.entries:
            link = getattr(entry, "link", None)
            if not link:
                continue
            published_at = _parse_date(entry)
            if published_at < since:
                continue
            yield RawDocument(
                source=self.name,
                source_url=link,
                title=getattr(entry, "title", "").strip(),
                published_at=published_at,
                raw_payload={
                    "guid": getattr(entry, "id", None),
                    "description": getattr(entry, "description", None),
                },
                fetched_at=now,
            )


def _parse_date(entry) -> datetime:
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if raw is None:
        return datetime.now(timezone.utc)
    parsed = dateparser.parse(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
```

EC-FISMA source takes `item_types: list[int]` and `topic_ids: list[int]` in its constructor and fetches from URLs constructed as:
```
https://ec.europa.eu/newsroom/fisma/feed?item_type_id={item_type}&lang=en&orderby=item_date
```
plus topic-specific feeds. Deduplicate by `link` across the combined feeds.

For each source: write a minimal RSS XML fixture (two items), patch `httpx_mock` or the `httpx.Client`, assert parsing, and commit.

```bash
git add regwatch/pipeline/fetch/esma_rss.py regwatch/pipeline/fetch/eba_rss.py \
        regwatch/pipeline/fetch/ec_fisma_rss.py tests/fixtures/esma_rss_sample.xml \
        tests/fixtures/eba_rss_sample.xml tests/fixtures/ec_fisma_rss_sample.xml \
        tests/unit/test_esma_rss_source.py tests/unit/test_eba_rss_source.py \
        tests/unit/test_ec_fisma_rss_source.py
git commit -m "feat(pipeline): add ESMA, EBA and EC-FISMA RSS sources"
```

### Task 24: CSSF consultation source

**Files:**
- Create: `regwatch/pipeline/fetch/cssf_consultation.py` (`name = "cssf_consultation"`)
- Create: `tests/unit/test_cssf_consultation_source.py`

This source polls the CSSF main feed (same URL pattern as `cssf_rss` but without the keyword filter, or the keyword `consultation` if available) and filters client-side on titles containing `consultation`, `feedback`, or `discussion paper`. It must also inspect `raw_payload["description"]` for those keywords.

Test pattern: reuse the `cssf_rss_sample.xml` fixture plus one additional item whose title includes "Consultation on XYZ". Assert that only consultation-titled items are yielded.

```bash
git commit -m "feat(pipeline): add CSSF consultation source with title heuristic"
```

---

## Phase 4 — Ollama integration for matching

### Task 25: Ollama HTTP client

**Files:**
- Create: `regwatch/ollama/__init__.py`
- Create: `regwatch/ollama/client.py`
- Create: `tests/unit/test_ollama_client.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from pytest_httpx import HTTPXMock

from regwatch.ollama.client import OllamaClient, OllamaError


def test_chat_returns_content(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"role": "assistant", "content": "Hello!"}, "done": True},
    )
    client = OllamaClient(base_url="http://localhost:11434", chat_model="llama3.1:8b",
                          embedding_model="nomic-embed-text")
    reply = client.chat(system="sys", user="hi")
    assert reply == "Hello!"


def test_embed_returns_vector(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/embed",
        json={"embeddings": [[0.1, 0.2, 0.3]]},
    )
    client = OllamaClient(base_url="http://localhost:11434", chat_model="x",
                          embedding_model="nomic-embed-text")
    vector = client.embed("some text")
    assert vector == [0.1, 0.2, 0.3]


def test_health_check(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "nomic-embed-text"}]},
    )
    client = OllamaClient(base_url="http://localhost:11434", chat_model="llama3.1:8b",
                          embedding_model="nomic-embed-text")
    status = client.health()
    assert status.reachable is True
    assert status.chat_model_available is True
    assert status.embedding_model_available is True


def test_health_unreachable(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx_mock.ConnectError("refused"))
    client = OllamaClient(base_url="http://localhost:11434", chat_model="x", embedding_model="y")
    status = client.health()
    assert status.reachable is False
```

- [ ] **Step 2: Implement `regwatch/ollama/__init__.py`** (blank) and `regwatch/ollama/client.py`:

```python
"""Thin HTTP client for a local Ollama instance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class OllamaError(RuntimeError):
    pass


@dataclass
class HealthStatus:
    reachable: bool
    chat_model_available: bool = False
    embedding_model_available: bool = False


class OllamaClient:
    def __init__(
        self,
        *,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._chat_model = chat_model
        self._embedding_model = embedding_model
        self._timeout = timeout

    def chat(self, *, system: str, user: str) -> str:
        payload = {
            "model": self._chat_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return self._post_for_chat(payload)

    def _post_for_chat(self, payload: dict[str, Any]) -> str:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        message = data.get("message", {})
        content = message.get("content", "")
        if not content:
            raise OllamaError("Empty response from Ollama chat endpoint")
        return content

    def chat_stream(self, *, system: str, user: str):
        """Yield content chunks from a streaming chat response."""
        payload = {
            "model": self._chat_model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        import json
        with httpx.Client(timeout=self._timeout) as client:
            with client.stream("POST", f"{self._base_url}/api/chat", json=payload) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    chunk = data.get("message", {}).get("content")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        return

    def embed(self, text: str) -> list[float]:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._embedding_model, "input": text},
            )
            response.raise_for_status()
            data = response.json()
        vectors = data.get("embeddings", [])
        if not vectors:
            raise OllamaError("Empty embeddings response")
        return list(vectors[0])

    def health(self) -> HealthStatus:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self._base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
        except Exception:  # noqa: BLE001
            return HealthStatus(reachable=False)
        names = {m.get("name", "") for m in data.get("models", [])}
        return HealthStatus(
            reachable=True,
            chat_model_available=self._chat_model in names
                or any(n.startswith(self._chat_model.split(":")[0]) for n in names),
            embedding_model_available=self._embedding_model in names
                or any(n.startswith(self._embedding_model.split(":")[0]) for n in names),
        )
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_ollama_client.py -v
git add regwatch/ollama tests/unit/test_ollama_client.py
git commit -m "feat(ollama): add Ollama HTTP client with health check"
```

### Task 26: Ollama reference extractor

**Files:**
- Create: `regwatch/pipeline/match/ollama_refs.py`
- Create: `tests/unit/test_ollama_refs.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import MagicMock

from regwatch.pipeline.match.ollama_refs import extract_references


def test_extracts_structured_refs_from_ollama() -> None:
    fake_client = MagicMock()
    fake_client.chat.return_value = '[{"ref": "CSSF 18/698", "context": "amendments"}, {"ref": "2022/2554", "context": "DORA"}]'

    refs = extract_references(fake_client, "this text amends CSSF 18/698 and refers to 2022/2554")

    assert len(refs) == 2
    assert refs[0]["ref"] == "CSSF 18/698"
    assert refs[1]["ref"] == "2022/2554"


def test_returns_empty_on_invalid_json() -> None:
    fake_client = MagicMock()
    fake_client.chat.return_value = "not valid json"
    assert extract_references(fake_client, "something") == []


def test_returns_empty_on_empty_input() -> None:
    fake_client = MagicMock()
    assert extract_references(fake_client, "") == []
    fake_client.chat.assert_not_called()
```

- [ ] **Step 2: Implement `regwatch/pipeline/match/ollama_refs.py`**

```python
"""Ollama-based extraction of regulatory references from free text."""
from __future__ import annotations

import json
import re

from regwatch.ollama.client import OllamaClient

_SYSTEM_PROMPT = (
    "You extract structured regulatory references from text. "
    "Output must be valid JSON and nothing else: "
    '[{"ref": "<identifier>", "context": "<surrounding phrase>"}, ...]. '
    "Identifiers include CSSF circular numbers (e.g. CSSF 18/698), "
    "EU regulation/directive numbers (e.g. 2022/2554, Directive (EU) 2024/927), "
    "CELEX IDs (e.g. 32022R2554), and ELI URIs. "
    "If no references are found return []."
)

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def extract_references(client: OllamaClient, text: str) -> list[dict[str, str]]:
    if not text or not text.strip():
        return []

    truncated = text[:8000]
    raw = client.chat(system=_SYSTEM_PROMPT, user=truncated)

    match = _JSON_ARRAY_RE.search(raw)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    cleaned: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict) and "ref" in item:
            cleaned.append(
                {"ref": str(item["ref"]), "context": str(item.get("context", ""))}
            )
    return cleaned
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_ollama_refs.py -v
git add regwatch/pipeline/match/ollama_refs.py tests/unit/test_ollama_refs.py
git commit -m "feat(pipeline): add Ollama-based reference extractor"
```

### Task 27: Combined matcher (rules + Ollama refs)

**Files:**
- Modify: `regwatch/pipeline/match/rules.py` — no change.
- Create: `regwatch/pipeline/match/combined.py`
- Create: `tests/unit/test_combined_matcher.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationAlias,
    RegulationType,
)
from regwatch.pipeline.match.combined import CombinedMatcher


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_reg(session: Session, reference: str, alias: str) -> int:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number=reference,
        title=reference,
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()
    session.add(RegulationAlias(regulation_id=reg.regulation_id, pattern=alias, kind="REGEX"))
    session.flush()
    return reg.regulation_id


def test_rule_match_found_skips_ollama(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rid = _add_reg(session, "CSSF 18/698", r"CSSF[\s\-]?18[/\-]698")
    session.commit()

    ollama = MagicMock()
    matcher = CombinedMatcher(session, ollama=ollama)
    refs = matcher.match("This cites CSSF 18/698 directly.")

    assert len(refs) == 1
    assert refs[0].regulation_id == rid
    ollama.chat.assert_not_called()


def test_ollama_referenced_then_resolved(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rid = _add_reg(session, "CSSF 18/698", r"CSSF[\s\-]?18[/\-]698")
    session.commit()

    ollama = MagicMock()
    ollama.chat.return_value = '[{"ref": "CSSF 18/698", "context": "amendment"}]'

    matcher = CombinedMatcher(session, ollama=ollama)
    refs = matcher.match("Long text without a literal match but the amendment intends to touch it.")

    assert len(refs) == 1
    assert refs[0].regulation_id == rid
    assert refs[0].method == "OLLAMA_REFERENCE"
```

- [ ] **Step 2: Implement `regwatch/pipeline/match/combined.py`**

```python
"""Combined matcher: rules first, then Ollama-extracted references, re-resolved through rules."""
from __future__ import annotations

from sqlalchemy.orm import Session

from regwatch.domain.types import MatchedReference
from regwatch.ollama.client import OllamaClient
from regwatch.pipeline.match.ollama_refs import extract_references
from regwatch.pipeline.match.rules import RuleMatcher


class CombinedMatcher:
    def __init__(self, session: Session, *, ollama: OllamaClient | None = None) -> None:
        self._rule_matcher = RuleMatcher(session)
        self._ollama = ollama

    def match(self, text: str) -> list[MatchedReference]:
        rule_matches = self._rule_matcher.match(text)
        if rule_matches:
            return rule_matches

        if self._ollama is None:
            return []

        extracted_refs = extract_references(self._ollama, text)
        if not extracted_refs:
            return []

        # Re-run the rule matcher on the extracted reference strings to
        # resolve them to regulation ids deterministically.
        results: list[MatchedReference] = []
        seen: set[int] = set()
        for item in extracted_refs:
            for hit in self._rule_matcher.match(item["ref"]):
                if hit.regulation_id not in seen:
                    seen.add(hit.regulation_id)
                    results.append(
                        MatchedReference(
                            regulation_id=hit.regulation_id,
                            method="OLLAMA_REFERENCE",
                            confidence=0.8,
                            snippet=item.get("context") or hit.snippet,
                        )
                    )
        return results
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_combined_matcher.py -v
git add regwatch/pipeline/match/combined.py tests/unit/test_combined_matcher.py
git commit -m "feat(pipeline): add combined matcher (rules + Ollama refs)"
```

### Task 28: Wire CombinedMatcher into the pipeline factory

**Files:**
- Modify: `regwatch/pipeline/pipeline_factory.py`
- Modify: `tests/integration/test_pipeline_end_to_end.py` (add a test with a mock Ollama)

- [ ] **Step 1: Write a new failing test case** in `tests/integration/test_pipeline_end_to_end.py`:

```python
def test_end_to_end_with_mock_ollama(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    session = _session(tmp_path)
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 18/698",
        title="IFM",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    reg.aliases.append(RegulationAlias(pattern=r"CSSF[\s\-]?18[/\-]698", kind="REGEX"))
    session.add(reg)
    session.commit()

    now = datetime.now(timezone.utc)
    # Text that rule matcher will NOT catch directly but Ollama extracts.
    raw = RawDocument(
        source="fake_end_to_end",
        source_url="https://example.com/z",
        title="Note on IFM governance amendments",
        published_at=now,
        raw_payload={
            "html_text": "This note modifies aspects of the existing IFM governance framework."
        },
        fetched_at=now,
    )

    fake_ollama = MagicMock()
    fake_ollama.chat.return_value = '[{"ref": "CSSF 18/698", "context": "IFM governance"}]'

    runner = build_runner(
        session,
        sources=[_FakeSource([raw])],
        archive_root=tmp_path / "pdfs",
        ollama_client=fake_ollama,
    )
    runner.run_once()
    session.commit()

    links = session.query(UpdateEventRegulationLink).all()
    assert len(links) == 1
    assert links[0].match_method == "OLLAMA_REFERENCE"
```

- [ ] **Step 2: Update `regwatch/pipeline/pipeline_factory.py`** to take an `ollama_client` parameter and use `CombinedMatcher`:

Replace the `_match` closure with one that uses `CombinedMatcher`:

```python
from regwatch.pipeline.match.combined import CombinedMatcher


def build_runner(
    session: Session,
    *,
    sources: Iterable,
    archive_root: Path | str,
    ollama_client=None,
) -> PipelineRunner:
    combined = CombinedMatcher(session, ollama=ollama_client)

    def _match(extracted: ExtractedDocument) -> MatchedDocument:
        text_for_match = (
            extracted.pdf_extracted_text or extracted.html_text or extracted.raw.title or ""
        )
        references = combined.match(text_for_match)
        is_ict = is_ict_document(extracted.raw.title + " " + (text_for_match or ""))
        lifecycle = classify_lifecycle(
            title=extracted.raw.title,
            celex_id=None,
            url=extracted.raw.source_url,
            application_date=None,
            today=date.today(),
        )
        severity = severity_for(
            title=extracted.raw.title,
            is_ict=is_ict,
            references_in_force=bool(references),
        )
        return MatchedDocument(
            extracted=extracted,
            references=references,
            lifecycle_stage=lifecycle,
            is_ict=is_ict,
            severity=severity,
        )

    # _extract unchanged from Task 20
    ...
    return PipelineRunner(session, sources=sources, extract=_extract, match=_match)
```

Remove the `ollama_enabled` parameter — it is superseded by `ollama_client=None`.

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/integration/test_pipeline_end_to_end.py -v
git add regwatch/pipeline/pipeline_factory.py tests/integration/test_pipeline_end_to_end.py
git commit -m "feat(pipeline): wire CombinedMatcher into pipeline factory"
```

---

## Phase 5 — RAG layer

### Task 29: Chunker

**Files:**
- Create: `regwatch/rag/__init__.py`
- Create: `regwatch/rag/chunker.py`
- Create: `tests/unit/test_chunker.py`

- [ ] **Step 1: Write the failing test**

```python
from regwatch.rag.chunker import Chunk, chunk_text


def test_chunks_short_text_into_one_chunk() -> None:
    chunks = chunk_text("Hello world.", chunk_size_tokens=500, overlap_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].text == "Hello world."


def test_chunks_long_text_with_overlap() -> None:
    paragraphs = "\n\n".join(f"Paragraph {i}. " + "word " * 100 for i in range(20))
    chunks = chunk_text(paragraphs, chunk_size_tokens=200, overlap_tokens=30)
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk.index == i
        assert chunk.token_count > 0
        assert chunk.token_count <= 250


def test_returns_empty_for_empty_text() -> None:
    assert chunk_text("", chunk_size_tokens=500, overlap_tokens=50) == []
    assert chunk_text("   ", chunk_size_tokens=500, overlap_tokens=50) == []
```

- [ ] **Step 2: Implement `regwatch/rag/__init__.py`** (blank) and `regwatch/rag/chunker.py`:

```python
"""Chunk long regulatory text into overlapping segments for vector indexing."""
from __future__ import annotations

from dataclasses import dataclass

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

_ENCODER = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    index: int
    text: str
    token_count: int


def chunk_text(text: str, *, chunk_size_tokens: int, overlap_tokens: int) -> list[Chunk]:
    if not text or not text.strip():
        return []

    # langchain splitter works on characters, so convert tokens → rough character budget.
    # ~4 characters per token is a safe heuristic for European-language text.
    chunk_size_chars = chunk_size_tokens * 4
    overlap_chars = overlap_tokens * 4

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size_chars,
        chunk_overlap=overlap_chars,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    pieces = splitter.split_text(text)

    chunks: list[Chunk] = []
    for i, piece in enumerate(pieces):
        tokens = len(_ENCODER.encode(piece))
        chunks.append(Chunk(index=i, text=piece, token_count=tokens))
    return chunks
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_chunker.py -v
git add regwatch/rag/chunker.py tests/unit/test_chunker.py
git commit -m "feat(rag): add chunker using langchain splitter and tiktoken"
```

### Task 30: Chunk indexing into sqlite-vec and FTS5

**Files:**
- Create: `regwatch/rag/indexing.py`
- Create: `tests/integration/test_indexing.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import text
from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentChunk,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.rag.indexing import index_version


def _session_with_vec(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    return Session(engine)


def _make_version(session: Session) -> DocumentVersion:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 18/698",
        title="IFM",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=False,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()

    v = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=1,
        is_current=True,
        fetched_at=datetime.now(timezone.utc),
        source_url="https://example.com",
        content_hash="x" * 64,
        html_text="First paragraph. Second paragraph about DORA and ICT risk.",
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(v)
    session.flush()
    return v


def test_index_version_writes_chunks_and_vectors(tmp_path: Path) -> None:
    session = _session_with_vec(tmp_path)
    version = _make_version(session)

    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [0.1, 0.2, 0.3, 0.4]

    index_version(
        session,
        version,
        ollama=fake_ollama,
        chunk_size_tokens=500,
        overlap_tokens=50,
        authorization_types=["AIFM", "CHAPTER15_MANCO"],
    )
    session.commit()

    chunks = session.query(DocumentChunk).all()
    assert len(chunks) >= 1

    with session.connection() as conn:
        count_vec = conn.execute(text("SELECT COUNT(*) FROM document_chunk_vec")).scalar()
        assert count_vec == len(chunks)
        count_fts = conn.execute(text("SELECT COUNT(*) FROM document_chunk_fts")).scalar()
        assert count_fts == len(chunks)
```

- [ ] **Step 2: Implement `regwatch/rag/indexing.py`**

```python
"""Chunk a DocumentVersion's text and write embeddings + FTS index rows."""
from __future__ import annotations

import json
import struct

from langdetect import detect
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from regwatch.db.models import DocumentChunk, DocumentVersion
from regwatch.ollama.client import OllamaClient
from regwatch.rag.chunker import chunk_text


def index_version(
    session: Session,
    version: DocumentVersion,
    *,
    ollama: OllamaClient,
    chunk_size_tokens: int,
    overlap_tokens: int,
    authorization_types: list[str],
) -> int:
    """Chunk the given version and write chunk rows, vector rows, and FTS rows.

    Returns the number of chunks created.
    """
    body = version.pdf_extracted_text or version.html_text or ""
    chunks = chunk_text(body, chunk_size_tokens=chunk_size_tokens, overlap_tokens=overlap_tokens)
    if not chunks:
        return 0

    try:
        language = detect(body[:2000])
    except Exception:  # noqa: BLE001
        language = None

    reg = version.regulation

    chunk_rows: list[DocumentChunk] = []
    for c in chunks:
        row = DocumentChunk(
            version_id=version.version_id,
            regulation_id=version.regulation_id,
            chunk_index=c.index,
            text=c.text,
            token_count=c.token_count,
            language=language,
            lifecycle_stage=reg.lifecycle_stage.value,
            is_ict=reg.is_ict,
            authorization_types=authorization_types,
        )
        session.add(row)
        chunk_rows.append(row)

    session.flush()

    for row, c in zip(chunk_rows, chunks, strict=True):
        vector = ollama.embed(c.text)
        packed = _pack_f32(vector)
        session.execute(
            sa_text("INSERT INTO document_chunk_vec(chunk_id, embedding) VALUES (:id, :vec)"),
            {"id": row.chunk_id, "vec": packed},
        )
        session.execute(
            sa_text("INSERT INTO document_chunk_fts(rowid, text) VALUES (:id, :text)"),
            {"id": row.chunk_id, "text": c.text},
        )

    return len(chunk_rows)


def _pack_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/integration/test_indexing.py -v
git add regwatch/rag/indexing.py tests/integration/test_indexing.py
git commit -m "feat(rag): add chunk indexing into sqlite-vec and FTS5"
```

### Task 31: Hybrid retrieval (dense + sparse + RRF)

**Files:**
- Create: `regwatch/rag/retrieval.py`
- Create: `tests/integration/test_retrieval.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.rag.indexing import index_version
from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters


def _setup(tmp_path: Path) -> tuple[Session, DocumentVersion]:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    session = Session(engine)

    reg = Regulation(
        type=RegulationType.EU_REGULATION,
        reference_number="DORA",
        title="DORA",
        issuing_authority="EU",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=True,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()

    version = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=1,
        is_current=True,
        fetched_at=datetime.now(timezone.utc),
        source_url="https://example.com",
        content_hash="y" * 64,
        html_text=(
            "DORA sets ICT risk management requirements. Article 24 TLPT rules apply. "
            "Third-party ICT risk register is mandatory."
        ),
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(version)
    session.flush()

    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [1.0, 0.0, 0.0, 0.0]
    index_version(
        session,
        version,
        ollama=fake_ollama,
        chunk_size_tokens=200,
        overlap_tokens=20,
        authorization_types=["AIFM", "CHAPTER15_MANCO"],
    )
    session.commit()
    return session, version


def test_dense_and_sparse_both_find_chunks(tmp_path: Path) -> None:
    session, version = _setup(tmp_path)
    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [1.0, 0.0, 0.0, 0.0]

    retriever = HybridRetriever(session, ollama=fake_ollama, top_k=5)
    hits = retriever.retrieve("Article 24 TLPT", RetrievalFilters())

    assert len(hits) >= 1
    assert any("Article 24" in h.text or "TLPT" in h.text for h in hits)


def test_ict_filter_applies(tmp_path: Path) -> None:
    session, version = _setup(tmp_path)
    fake_ollama = MagicMock()
    fake_ollama.embed.return_value = [1.0, 0.0, 0.0, 0.0]

    retriever = HybridRetriever(session, ollama=fake_ollama, top_k=5)
    hits = retriever.retrieve("ICT", RetrievalFilters(is_ict=True))
    assert all(h.is_ict for h in hits)
```

- [ ] **Step 2: Implement `regwatch/rag/retrieval.py`**

```python
"""Hybrid retrieval: dense sqlite-vec + sparse FTS5 merged by reciprocal rank fusion."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from regwatch.db.models import DocumentChunk
from regwatch.ollama.client import OllamaClient


@dataclass
class RetrievalFilters:
    is_ict: bool | None = None
    authorization_type: str | None = None  # "AIFM" or "CHAPTER15_MANCO"
    lifecycle_stages: list[str] = field(default_factory=list)
    regulation_ids: list[int] = field(default_factory=list)


@dataclass
class RetrievedChunk:
    chunk_id: int
    version_id: int
    regulation_id: int
    text: str
    is_ict: bool
    lifecycle_stage: str
    score: float


class HybridRetriever:
    def __init__(self, session: Session, *, ollama: OllamaClient, top_k: int = 20) -> None:
        self._session = session
        self._ollama = ollama
        self._top_k = top_k

    def retrieve(self, query: str, filters: RetrievalFilters) -> list[RetrievedChunk]:
        query_vec = self._ollama.embed(query)
        dense_hits = self._dense_search(query_vec, filters)
        sparse_hits = self._sparse_search(query, filters)
        fused_ids = _reciprocal_rank_fusion(dense_hits, sparse_hits, k=60)
        return self._hydrate(fused_ids[: self._top_k])

    def _dense_search(self, vec: list[float], filters: RetrievalFilters) -> list[int]:
        packed = struct.pack(f"{len(vec)}f", *vec)
        query = sa_text(
            """
            SELECT cv.chunk_id
            FROM document_chunk_vec cv
            JOIN document_chunk c ON c.chunk_id = cv.chunk_id
            WHERE cv.embedding MATCH :vec
              AND k = :k
              AND (:is_ict IS NULL OR c.is_ict = :is_ict)
              AND (:stage_count = 0 OR c.lifecycle_stage IN :stages)
            ORDER BY cv.distance
            """
        )
        stages = tuple(filters.lifecycle_stages) if filters.lifecycle_stages else ("__none__",)
        rows = (
            self._session.execute(
                query.bindparams(
                    sa_text("stages").bindparams(expanding=True) if False else sa_text(":stages"),
                ),
                {
                    "vec": packed,
                    "k": self._top_k,
                    "is_ict": filters.is_ict,
                    "stage_count": len(filters.lifecycle_stages),
                    "stages": stages,
                },
            )
            .scalars()
            .all()
        )
        return list(rows)

    def _sparse_search(self, query: str, filters: RetrievalFilters) -> list[int]:
        # Escape FTS special characters.
        safe_query = query.replace('"', " ").strip()
        if not safe_query:
            return []
        query_sql = sa_text(
            """
            SELECT f.rowid
            FROM document_chunk_fts f
            JOIN document_chunk c ON c.chunk_id = f.rowid
            WHERE f.text MATCH :q
              AND (:is_ict IS NULL OR c.is_ict = :is_ict)
            ORDER BY bm25(document_chunk_fts)
            LIMIT :k
            """
        )
        rows = (
            self._session.execute(
                query_sql,
                {"q": safe_query, "is_ict": filters.is_ict, "k": self._top_k},
            )
            .scalars()
            .all()
        )
        return list(rows)

    def _hydrate(self, chunk_ids: list[int]) -> list[RetrievedChunk]:
        if not chunk_ids:
            return []
        rows = (
            self._session.query(DocumentChunk)
            .filter(DocumentChunk.chunk_id.in_(chunk_ids))
            .all()
        )
        by_id = {r.chunk_id: r for r in rows}
        out: list[RetrievedChunk] = []
        for i, cid in enumerate(chunk_ids):
            r = by_id.get(cid)
            if r is None:
                continue
            out.append(
                RetrievedChunk(
                    chunk_id=r.chunk_id,
                    version_id=r.version_id,
                    regulation_id=r.regulation_id,
                    text=r.text,
                    is_ict=r.is_ict,
                    lifecycle_stage=r.lifecycle_stage,
                    score=1.0 / (i + 1),
                )
            )
        return out


def _reciprocal_rank_fusion(
    dense: list[int], sparse: list[int], *, k: int = 60
) -> list[int]:
    scores: dict[int, float] = {}
    for rank, cid in enumerate(dense):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    for rank, cid in enumerate(sparse):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]
```

**Note:** The dense search SQL uses parameter binding. sqlite-vec requires `k = N` in the WHERE clause; expand the filter bindparam list carefully because SQLite expanding bind params in mixed WHERE clauses can be fiddly. If the test fails because of binding issues, simplify by running the base dense query without filters first and then filtering in Python over the candidate set.

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/integration/test_retrieval.py -v
git add regwatch/rag/retrieval.py tests/integration/test_retrieval.py
git commit -m "feat(rag): add hybrid retrieval (dense + sparse + RRF)"
```

### Task 32: Answer generation with citations

**Files:**
- Create: `regwatch/rag/answer.py`
- Create: `tests/unit/test_rag_answer.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import MagicMock

from regwatch.rag.answer import AnswerRequest, generate_answer
from regwatch.rag.retrieval import RetrievedChunk


def test_generate_answer_with_chunks() -> None:
    chunks = [
        RetrievedChunk(
            chunk_id=1,
            version_id=10,
            regulation_id=100,
            text="Article 24 of DORA requires ICT risk assessments.",
            is_ict=True,
            lifecycle_stage="IN_FORCE",
            score=0.9,
        )
    ]
    ollama = MagicMock()
    ollama.chat.return_value = "Under Article 24 of DORA, ICT risk assessments are required (chunk 1)."

    req = AnswerRequest(question="What does Article 24 DORA require?", chunks=chunks)
    response = generate_answer(ollama, req)

    assert "Article 24" in response.answer
    assert response.cited_chunk_ids == [1]


def test_generate_answer_declines_without_chunks() -> None:
    ollama = MagicMock()
    req = AnswerRequest(question="Anything?", chunks=[])
    response = generate_answer(ollama, req)

    assert "could not find" in response.answer.lower()
    ollama.chat.assert_not_called()
    assert response.cited_chunk_ids == []
```

- [ ] **Step 2: Implement `regwatch/rag/answer.py`**

```python
"""Generate grounded answers from retrieved chunks via Ollama."""
from __future__ import annotations

from dataclasses import dataclass

from regwatch.ollama.client import OllamaClient
from regwatch.rag.retrieval import RetrievedChunk

_SYSTEM_PROMPT = (
    "You are a regulatory assistant for a Luxembourg fund management company. "
    "Answer ONLY using the context provided below. "
    "If the context does not contain the answer, say 'The provided context does not contain an answer.' "
    "Cite sources in your answer as (chunk <chunk_id>)."
)


@dataclass
class AnswerRequest:
    question: str
    chunks: list[RetrievedChunk]


@dataclass
class AnswerResponse:
    answer: str
    cited_chunk_ids: list[int]


def generate_answer(ollama: OllamaClient, request: AnswerRequest) -> AnswerResponse:
    if not request.chunks:
        return AnswerResponse(
            answer="I could not find relevant information in the indexed regulations.",
            cited_chunk_ids=[],
        )

    context_blocks = "\n\n".join(
        f"[chunk {c.chunk_id} | regulation_id={c.regulation_id}]\n{c.text}"
        for c in request.chunks
    )
    user_prompt = f"Context:\n{context_blocks}\n\nQuestion: {request.question}"

    answer = ollama.chat(system=_SYSTEM_PROMPT, user=user_prompt)
    cited_ids = [c.chunk_id for c in request.chunks]
    return AnswerResponse(answer=answer, cited_chunk_ids=cited_ids)
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_rag_answer.py -v
git add regwatch/rag/answer.py tests/unit/test_rag_answer.py
git commit -m "feat(rag): add answer generation with chunk citations"
```

### Task 33: Chat session persistence

**Files:**
- Create: `regwatch/rag/chat_service.py`
- Create: `tests/integration/test_chat_service.py`

The ChatService owns `chat_session` and `chat_message` tables, tracks active filters per session, and wires retrieval + answer generation.

Write tests that:
1. Create a session, send a user message, assert an assistant message was stored with retrieved_chunk_ids.
2. Load an existing session and assert message history is returned in order.

Implementation skeleton:

```python
"""Chat service: ties together retrieval, answer generation, and persistence."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from regwatch.db.models import ChatMessage, ChatSession
from regwatch.ollama.client import OllamaClient
from regwatch.rag.answer import AnswerRequest, generate_answer
from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters


class ChatService:
    def __init__(self, session: Session, ollama: OllamaClient, top_k: int = 10) -> None:
        self._session = session
        self._ollama = ollama
        self._retriever = HybridRetriever(session, ollama=ollama, top_k=top_k)

    def create_session(self, title: str, filters: RetrievalFilters) -> ChatSession:
        row = ChatSession(
            title=title,
            created_at=datetime.now(timezone.utc),
            filters=asdict(filters),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def ask(self, session_id: int, question: str) -> ChatMessage:
        cs = self._session.get(ChatSession, session_id)
        if cs is None:
            raise ValueError(f"ChatSession {session_id} not found")
        filters = RetrievalFilters(**cs.filters)

        self._session.add(
            ChatMessage(
                session_id=session_id,
                role="user",
                content=question,
                retrieved_chunk_ids=[],
                created_at=datetime.now(timezone.utc),
            )
        )
        self._session.flush()

        chunks = self._retriever.retrieve(question, filters)
        result = generate_answer(self._ollama, AnswerRequest(question=question, chunks=chunks))

        assistant = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=result.answer,
            retrieved_chunk_ids=result.cited_chunk_ids,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(assistant)
        self._session.flush()
        return assistant

    def list_messages(self, session_id: int) -> list[ChatMessage]:
        return (
            self._session.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
            .all()
        )
```

Write the two integration tests, run, commit:

```bash
git commit -m "feat(rag): add ChatService with session persistence"
```

---

## Phase 6 — Scheduler

### Task 34: APScheduler setup with jobs and startup assertion

**Files:**
- Create: `regwatch/scheduler/__init__.py`
- Create: `regwatch/scheduler/jobs.py`
- Create: `tests/unit/test_scheduler_jobs.py`

- [ ] **Step 1: Write the failing test**

```python
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
    cfg = _minimal_config({"unknown_source": SourceConfig(enabled=True, interval_hours=6)})
    with pytest.raises(ValueError, match="unknown_source"):
        assert_sources_have_jobs(cfg)


def test_disabled_source_is_not_required_to_have_job() -> None:
    cfg = _minimal_config({"unknown_source": SourceConfig(enabled=False, interval_hours=6)})
    assert_sources_have_jobs(cfg)  # must not raise


def test_build_scheduler_returns_running_scheduler() -> None:
    cfg = _minimal_config(
        {
            "cssf_rss": SourceConfig(enabled=True, interval_hours=6, keywords=["aif"]),
        }
    )
    fake_run = MagicMock()
    scheduler = build_scheduler(cfg, run_pipeline_for=fake_run, start=False)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "run_pipeline_cssf" in job_ids
```

- [ ] **Step 2: Implement `regwatch/scheduler/__init__.py`** (blank) and `regwatch/scheduler/jobs.py`:

```python
"""APScheduler configuration: source-to-job mapping and job builder."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from regwatch.config import AppConfig

# Maps each source name to the logical job it runs in.
SOURCE_TO_JOB: dict[str, str] = {
    "cssf_rss": "run_pipeline_cssf",
    "cssf_consultation": "run_pipeline_cssf",
    "eur_lex_adopted": "run_pipeline_eu",
    "eur_lex_proposal": "run_pipeline_eu",
    "legilux_sparql": "run_pipeline_lu",
    "legilux_parliamentary": "run_pipeline_lu",
    "esma_rss": "run_pipeline_esma_eba_fisma",
    "eba_rss": "run_pipeline_esma_eba_fisma",
    "ec_fisma_rss": "run_pipeline_esma_eba_fisma",
}


def assert_sources_have_jobs(config: AppConfig) -> None:
    for name, source_cfg in config.sources.items():
        if source_cfg.enabled and name not in SOURCE_TO_JOB:
            raise ValueError(
                f"Enabled source {name!r} has no job mapping in SOURCE_TO_JOB. "
                "Register it before starting the scheduler."
            )


def build_scheduler(
    config: AppConfig,
    *,
    run_pipeline_for: Callable[[list[str]], Any],
    start: bool = True,
) -> BackgroundScheduler:
    """Create an APScheduler with one job per active pipeline group.

    `run_pipeline_for(source_names)` is the callback that the scheduler invokes.
    """
    assert_sources_have_jobs(config)

    scheduler = BackgroundScheduler(timezone=config.ui.timezone)

    grouped: dict[str, list[str]] = {}
    grouped_interval: dict[str, int] = {}
    for source_name, source_cfg in config.sources.items():
        if not source_cfg.enabled:
            continue
        job_name = SOURCE_TO_JOB[source_name]
        grouped.setdefault(job_name, []).append(source_name)
        # Use the minimum interval of any source in the group.
        prev = grouped_interval.get(job_name)
        if prev is None or source_cfg.interval_hours < prev:
            grouped_interval[job_name] = source_cfg.interval_hours

    for job_name, sources in grouped.items():
        scheduler.add_job(
            run_pipeline_for,
            trigger=IntervalTrigger(hours=grouped_interval[job_name]),
            id=job_name,
            name=job_name,
            args=(sources,),
            replace_existing=True,
        )

    if start:
        scheduler.start()
    return scheduler
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_scheduler_jobs.py -v
git add regwatch/scheduler tests/unit/test_scheduler_jobs.py
git commit -m "feat(scheduler): add APScheduler jobs with startup assertion"
```

---

## Phase 7 — Services layer

Each service owns a single use case and is called by CLI commands and FastAPI routes. Services accept a `Session` at construction time and expose methods returning plain dataclasses (not ORM rows) so the web layer never touches ORM internals directly.

### Task 35: RegulationService

**Files:**
- Create: `regwatch/services/__init__.py`
- Create: `regwatch/services/regulations.py`
- Create: `tests/unit/test_regulation_service.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationType,
)
from regwatch.services.regulations import RegulationFilter, RegulationService


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session) -> None:
    def add(ref: str, auth: str, is_ict: bool, stage: LifecycleStage) -> None:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number=ref,
            title=ref,
            issuing_authority="CSSF",
            lifecycle_stage=stage,
            is_ict=is_ict,
            source_of_truth="SEED",
            url="https://example.com",
        )
        reg.applicabilities.append(
            RegulationApplicability(authorization_type=auth)
        )
        session.add(reg)

    add("CSSF 18/698", "BOTH", False, LifecycleStage.IN_FORCE)
    add("CSSF 23/844", "AIFM", False, LifecycleStage.IN_FORCE)
    add("CSSF 11/512", "CHAPTER15_MANCO", False, LifecycleStage.IN_FORCE)
    add("DORA", "BOTH", True, LifecycleStage.IN_FORCE)
    add("AIFMD II", "BOTH", False, LifecycleStage.ADOPTED_NOT_IN_FORCE)
    session.commit()


def test_list_all(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed(session)
    svc = RegulationService(session)
    assert len(svc.list(RegulationFilter())) == 5


def test_filter_by_aifm_includes_both(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed(session)
    svc = RegulationService(session)
    aifm = svc.list(RegulationFilter(authorization_type="AIFM"))
    refs = {r.reference_number for r in aifm}
    assert "CSSF 18/698" in refs  # BOTH
    assert "CSSF 23/844" in refs  # AIFM
    assert "CSSF 11/512" not in refs  # MANCO-only


def test_filter_by_is_ict(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed(session)
    svc = RegulationService(session)
    ict = svc.list(RegulationFilter(is_ict=True))
    assert len(ict) == 1
    assert ict[0].reference_number == "DORA"


def test_get_by_reference(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _seed(session)
    svc = RegulationService(session)
    reg = svc.get_by_reference("CSSF 18/698")
    assert reg is not None
    assert reg.title == "CSSF 18/698"
```

- [ ] **Step 2: Implement `regwatch/services/__init__.py`** (blank) and `regwatch/services/regulations.py`:

```python
"""Regulation catalog queries exposed to the UI layer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from regwatch.db.models import (
    LifecycleStage,
    Regulation,
    RegulationApplicability,
)


@dataclass
class RegulationFilter:
    authorization_type: Literal["AIFM", "CHAPTER15_MANCO"] | None = None
    is_ict: bool | None = None
    lifecycle_stages: list[str] | None = None
    search: str | None = None


@dataclass
class RegulationDTO:
    regulation_id: int
    reference_number: str
    title: str
    type: str
    issuing_authority: str
    lifecycle_stage: str
    is_ict: bool
    url: str
    transposition_deadline: date | None
    application_date: date | None


class RegulationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, flt: RegulationFilter) -> list[RegulationDTO]:
        query = self._session.query(Regulation)

        if flt.authorization_type:
            query = query.join(RegulationApplicability).filter(
                or_(
                    RegulationApplicability.authorization_type == flt.authorization_type,
                    RegulationApplicability.authorization_type == "BOTH",
                )
            )
        if flt.is_ict is not None:
            query = query.filter(Regulation.is_ict == flt.is_ict)
        if flt.lifecycle_stages:
            query = query.filter(
                Regulation.lifecycle_stage.in_(
                    [LifecycleStage(s) for s in flt.lifecycle_stages]
                )
            )
        if flt.search:
            like = f"%{flt.search}%"
            query = query.filter(
                or_(
                    Regulation.reference_number.ilike(like),
                    Regulation.title.ilike(like),
                )
            )

        rows = query.order_by(Regulation.reference_number).all()
        return [_to_dto(r) for r in rows]

    def get_by_reference(self, reference: str) -> RegulationDTO | None:
        reg = (
            self._session.query(Regulation)
            .filter(Regulation.reference_number == reference)
            .one_or_none()
        )
        return _to_dto(reg) if reg is not None else None


def _to_dto(r: Regulation) -> RegulationDTO:
    return RegulationDTO(
        regulation_id=r.regulation_id,
        reference_number=r.reference_number,
        title=r.title,
        type=r.type.value,
        issuing_authority=r.issuing_authority,
        lifecycle_stage=r.lifecycle_stage.value,
        is_ict=r.is_ict,
        url=r.url,
        transposition_deadline=r.transposition_deadline,
        application_date=r.application_date,
    )
```

- [ ] **Step 3: Run tests and commit**

```bash
pytest tests/unit/test_regulation_service.py -v
git add regwatch/services tests/unit/test_regulation_service.py
git commit -m "feat(services): add RegulationService with filters"
```

### Task 36: InboxService

**Files:**
- Create: `regwatch/services/inbox.py`
- Create: `tests/unit/test_inbox_service.py`

Handles listing new/reviewed events, filtering by severity and source, and marking events as seen/archived.

Key API:

```python
class InboxService:
    def list_new(self) -> list[UpdateEventDTO]: ...
    def list_by_severity(self, severity: str) -> list[UpdateEventDTO]: ...
    def count_new(self) -> int: ...
    def mark_seen(self, event_id: int) -> None: ...
    def archive(self, event_id: int) -> None: ...
```

Test the following behaviours: count_new ignores SEEN/ARCHIVED, mark_seen sets seen_at timestamp, archive transitions NEW → ARCHIVED, list_new returns events sorted by severity then published_at DESC.

Commit:
```bash
git commit -m "feat(services): add InboxService with triage actions"
```

### Task 37: UpdateService (event detail + version diffs)

**Files:**
- Create: `regwatch/services/updates.py`
- Create: `tests/unit/test_update_service.py`

Key API:
- `get_event(event_id: int) -> EventDetailDTO` returns an event with linked regulations, latest version of each, and the `change_summary` diff text.
- `list_versions(regulation_id: int) -> list[VersionDTO]` returns all versions of a regulation ordered by `version_number`.
- `compare_versions(regulation_id: int, a: int, b: int) -> DiffDTO` returns a freshly computed diff (via `compute_diff` from Task 17) between two specific version numbers — useful when the user wants to compare non-adjacent versions.

Test: a regulation with three versions, `compare_versions(reg_id, 1, 3)` returns a diff containing text from version 3 additions.

Commit:
```bash
git commit -m "feat(services): add UpdateService for events and version diffs"
```

### Task 38: DeadlineService

**Files:**
- Create: `regwatch/services/deadlines.py`
- Create: `tests/unit/test_deadline_service.py`

Key API:
- `upcoming(window_days: int) -> list[DeadlineDTO]` returns every regulation whose `transposition_deadline` or `application_date` falls within the next `window_days`, tagged with `kind` ("TRANSPOSITION" / "APPLICATION") and `days_until`.
- `severity_band(days_until: int) -> str` returns one of `OVERDUE`, `RED`, `AMBER`, `BLUE`, `GREY` per the bands in the spec.

Test cases: a seeded regulation with `transposition_deadline = today + 10` shows up as RED; `application_date = today + 365` shows up as BLUE; past deadlines show up as OVERDUE.

Commit:
```bash
git commit -m "feat(services): add DeadlineService with severity bands"
```

### Task 39: ChatService pointer

ChatService was already implemented in Task 33. In this phase, simply move it (or re-export) under `regwatch/services/chat.py` so the web layer imports all services from one package. Update all imports.

```bash
git commit -m "refactor(services): re-home ChatService under services package"
```

---

## Phase 8 — Web UI

### Task 40: FastAPI app skeleton, lifespan, base layout

**Files:**
- Create: `regwatch/main.py`
- Create: `regwatch/web/__init__.py`
- Create: `regwatch/web/routes/__init__.py`
- Create: `regwatch/web/templates/base.html`
- Create: `regwatch/web/templates/partials/sidebar.html`
- Create: `regwatch/web/static/.gitkeep`
- Create: `tests/integration/test_app_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from regwatch.main import create_app


def test_root_returns_dashboard(tmp_path, monkeypatch):
    # Use the config.example.yaml against a temp db.
    import shutil
    shutil.copy("config.example.yaml", tmp_path / "config.yaml")
    # Override paths to tmp_path
    import yaml
    cfg_path = tmp_path / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["paths"]["db_file"] = str(tmp_path / "app.db")
    data["paths"]["pdf_archive"] = str(tmp_path / "pdfs")
    data["paths"]["uploads_dir"] = str(tmp_path / "uploads")
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "uploads").mkdir()
    cfg_path.write_text(yaml.safe_dump(data))

    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))

    app = create_app()
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "RegWatch" in response.text
```

- [ ] **Step 2: Implement `regwatch/main.py`**

```python
"""FastAPI application factory."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from regwatch.config import AppConfig, load_config
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.scheduler.jobs import build_scheduler

_TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"
_STATIC_DIR = Path(__file__).parent / "web" / "static"


def create_app() -> FastAPI:
    config_path = Path(os.environ.get("REGWATCH_CONFIG", "config.yaml"))
    config = load_config(config_path)

    engine = create_app_engine(config.paths.db_file)
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=config.ollama.embedding_dim)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = build_scheduler(
            config,
            run_pipeline_for=lambda sources: None,  # wired in Task 41
            start=False,
        )
        app.state.scheduler = scheduler
        app.state.config = config
        app.state.session_factory = session_factory
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="Regulatory Watcher", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from regwatch.web.routes import dashboard
    app.include_router(dashboard.router)

    return app


app = create_app()
```

- [ ] **Step 3: Create `regwatch/web/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}RegWatch{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.13.5/dist/cdn.min.js"></script>
</head>
<body class="bg-slate-50 text-slate-800">
  <div class="flex min-h-screen">
    {% include "partials/sidebar.html" %}
    <main class="flex-1 p-6">
      {% block content %}{% endblock %}
    </main>
  </div>
</body>
</html>
```

- [ ] **Step 4: Create `regwatch/web/templates/partials/sidebar.html`**

```html
<aside class="w-56 bg-slate-900 text-slate-100 min-h-screen p-4">
  <div class="text-xl font-bold mb-6">RegWatch</div>
  <nav class="flex flex-col gap-1 text-sm">
    <a href="/" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'dashboard' %}bg-slate-800{% endif %}">📊 Dashboard</a>
    <a href="/inbox" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'inbox' %}bg-slate-800{% endif %}">📬 Inbox</a>
    <a href="/catalog" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'catalog' %}bg-slate-800{% endif %}">📋 Catalog</a>
    <a href="/catalog?authorization=AIFM" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">🧭 AIFM</a>
    <a href="/catalog?authorization=CHAPTER15_MANCO" class="px-5 py-1 rounded hover:bg-slate-800 text-slate-300">🧭 Chapter 15 ManCo</a>
    <a href="/ict" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'ict' %}bg-slate-800{% endif %}">⚡ ICT / DORA</a>
    <a href="/drafts" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'drafts' %}bg-slate-800{% endif %}">📝 Drafts</a>
    <a href="/deadlines" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'deadlines' %}bg-slate-800{% endif %}">⏰ Deadlines</a>
    <a href="/chat" class="px-3 py-2 rounded hover:bg-slate-800 {% if active == 'chat' %}bg-slate-800{% endif %}">💬 Q&amp;A</a>
    <a href="/settings" class="mt-auto px-3 py-2 rounded hover:bg-slate-800 {% if active == 'settings' %}bg-slate-800{% endif %}">⚙ Settings</a>
  </nav>
</aside>
```

- [ ] **Step 5: Create the dashboard route** `regwatch/web/routes/dashboard.py`:

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"active": "dashboard", "kpis": {"catalog": 0, "inbox": 0, "drafts": 0, "ict": 0}},
    )
```

- [ ] **Step 6: Create a minimal `regwatch/web/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}RegWatch — Dashboard{% endblock %}
{% block content %}
  <h1 class="text-2xl font-bold mb-4">Dashboard</h1>
  <div class="grid grid-cols-4 gap-4">
    <div class="bg-white p-4 rounded shadow-sm border">
      <div class="text-xs uppercase text-slate-500">Catalog</div>
      <div class="text-2xl font-bold">{{ kpis.catalog }}</div>
    </div>
    <div class="bg-white p-4 rounded shadow-sm border">
      <div class="text-xs uppercase text-slate-500">Inbox</div>
      <div class="text-2xl font-bold text-red-600">{{ kpis.inbox }}</div>
    </div>
    <div class="bg-white p-4 rounded shadow-sm border">
      <div class="text-xs uppercase text-slate-500">Drafts</div>
      <div class="text-2xl font-bold text-amber-600">{{ kpis.drafts }}</div>
    </div>
    <div class="bg-white p-4 rounded shadow-sm border">
      <div class="text-xs uppercase text-slate-500">ICT / DORA</div>
      <div class="text-2xl font-bold text-purple-600">{{ kpis.ict }}</div>
    </div>
  </div>
{% endblock %}
```

- [ ] **Step 7: Run tests and commit**

```bash
pytest tests/integration/test_app_smoke.py -v
git add regwatch/main.py regwatch/web tests/integration/test_app_smoke.py
git commit -m "feat(web): add FastAPI app with dashboard skeleton"
```

### Task 41: Dashboard view with real KPIs and widgets

**Files:**
- Modify: `regwatch/web/routes/dashboard.py`
- Modify: `regwatch/web/templates/dashboard.html`
- Create: `tests/integration/test_dashboard_view.py`

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from tests.integration.test_app_smoke import ...  # re-use setup helper
# Seed data via the services, then:
def test_dashboard_shows_kpi_counts(...):
    # seed 3 regulations (2 in-force, 1 draft), 2 inbox events, 1 ICT regulation
    response = client.get("/")
    assert "Dashboard" in response.text
    # Assert the counts appear in the HTML
```

- [ ] **Step 2: Update the route** to use `RegulationService`, `InboxService`, and `DeadlineService`:

```python
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from regwatch.services.deadlines import DeadlineService
from regwatch.services.inbox import InboxService
from regwatch.services.regulations import RegulationFilter, RegulationService

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        reg_svc = RegulationService(session)
        inbox_svc = InboxService(session)
        deadline_svc = DeadlineService(session)

        all_regs = reg_svc.list(RegulationFilter())
        ict_regs = reg_svc.list(RegulationFilter(is_ict=True))
        drafts = reg_svc.list(
            RegulationFilter(
                lifecycle_stages=[
                    "CONSULTATION",
                    "PROPOSAL",
                    "DRAFT_BILL",
                    "ADOPTED_NOT_IN_FORCE",
                ]
            )
        )
        upcoming = deadline_svc.upcoming(window_days=730)
        inbox_count = inbox_svc.count_new()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "kpis": {
                "catalog": len([r for r in all_regs if r.lifecycle_stage == "IN_FORCE"]),
                "inbox": inbox_count,
                "drafts": len(drafts),
                "ict": len(ict_regs),
            },
            "upcoming": upcoming[:5],
        },
    )
```

- [ ] **Step 3: Update `dashboard.html`** to include the "Upcoming deadlines" widget and "Recent activity" placeholder. Add HTMX polling attributes where appropriate.

- [ ] **Step 4: Run and commit**

```bash
pytest tests/integration/test_dashboard_view.py -v
git commit -m "feat(web): wire dashboard view to services"
```

### Task 42: Inbox view with HTMX triage

**Files:**
- Create: `regwatch/web/routes/inbox.py`
- Create: `regwatch/web/templates/inbox/list.html`
- Create: `regwatch/web/templates/inbox/detail.html`
- Create: `regwatch/web/templates/partials/inbox_row.html`
- Create: `tests/integration/test_inbox_view.py`

Routes:
- `GET /inbox` — list view
- `GET /inbox/{event_id}` — detail pane (HTMX target)
- `POST /inbox/{event_id}/mark-seen` — HTMX action, returns the updated row partial
- `POST /inbox/{event_id}/archive` — HTMX action, returns empty response and `hx-swap="outerHTML"` removes the row

The list template uses HTMX `hx-get` on each row to load the detail pane; the detail pane has buttons posting to the action routes with `hx-target="closest tr"` and `hx-swap="outerHTML"`.

Tests assert:
1. GET `/inbox` returns 200 and contains event titles.
2. POST `/inbox/{id}/mark-seen` updates the row status to SEEN.
3. POST `/inbox/{id}/archive` removes the event from the NEW list.

Commit:
```bash
git commit -m "feat(web): add inbox view with HTMX triage"
```

### Task 43: Catalog view with filters

**Files:**
- Create: `regwatch/web/routes/catalog.py`
- Create: `regwatch/web/templates/catalog/list.html`
- Create: `regwatch/web/templates/partials/catalog_row.html`
- Create: `tests/integration/test_catalog_view.py`

Routes:
- `GET /catalog?authorization=&search=&lifecycle=` — reads query params into `RegulationFilter`, renders a table with filter controls at the top. Filters are plain `<form method="get">` so URLs are shareable.

Test that the authorization filter returns only applicable regulations.

```bash
git commit -m "feat(web): add catalog view with filters"
```

### Task 44: Regulation detail view with diff and timeline

**Files:**
- Create: `regwatch/web/routes/regulation_detail.py`
- Create: `regwatch/web/templates/regulation/detail.html`
- Create: `tests/integration/test_regulation_detail_view.py`

Route: `GET /regulations/{regulation_id}`

The template shows:
- Header with lifecycle badge, applicability tags, version count.
- Main column: latest diff (from `document_version.change_summary`), rendered with simple HTML highlighting (lines starting with `+` green, `-` red), and a list of linked update events.
- Side column: lifecycle timeline — list of `document_version` rows with publication dates, plus `regulation_lifecycle_link` rows as "expected changes".

Test: GET `/regulations/{id}` returns 200, contains the regulation title, and shows at least one version row.

```bash
git commit -m "feat(web): add regulation detail view with diff and timeline"
```

### Task 45: Drafts, Deadlines, and ICT views

**Files:**
- Create: `regwatch/web/routes/drafts.py` — reuses `RegulationService` with `lifecycle_stages=[CONSULTATION, PROPOSAL, DRAFT_BILL, ADOPTED_NOT_IN_FORCE]`.
- Create: `regwatch/web/routes/deadlines.py` — uses `DeadlineService.upcoming(window_days=730)`, renders with severity band colours.
- Create: `regwatch/web/routes/ict.py` — uses `RegulationService` with `is_ict=True`, offers filter chips per DORA pillar.
- Create: `regwatch/web/templates/drafts/list.html`
- Create: `regwatch/web/templates/deadlines/list.html`
- Create: `regwatch/web/templates/ict/list.html`
- Create: `tests/integration/test_drafts_deadlines_ict_views.py`

Each view is a GET endpoint that calls one service and renders a table. Reuse `partials/catalog_row.html` where possible to DRY the markup.

Test each endpoint returns 200 and contains expected content.

```bash
git commit -m "feat(web): add drafts, deadlines and ICT views"
```

### Task 46: Chat view with SSE streaming

**Files:**
- Create: `regwatch/web/routes/chat.py`
- Create: `regwatch/web/templates/chat/list.html`
- Create: `regwatch/web/templates/chat/session.html`
- Create: `tests/integration/test_chat_view.py`

Routes:
- `GET /chat` — lists `ChatSession` rows, form to create a new session with filter controls.
- `POST /chat` — creates a new session, redirects to `/chat/{id}`.
- `GET /chat/{session_id}` — renders the chat log plus an input form.
- `POST /chat/{session_id}/ask` — accepts a question, returns a streaming `text/event-stream` response that yields tokens from `OllamaClient.chat_stream` and persists the final assistant message.

Streaming endpoint skeleton:

```python
@router.post("/chat/{session_id}/ask")
async def ask(session_id: int, request: Request, question: str = Form(...)):
    from starlette.responses import StreamingResponse
    def _gen():
        with request.app.state.session_factory() as session:
            # Retrieve chunks, start streaming via ollama.chat_stream,
            # accumulate full text, then persist ChatMessage at the end.
            ...
    return StreamingResponse(_gen(), media_type="text/event-stream")
```

Test: GET `/chat` returns 200 and the session form is present. POST `/chat/{id}/ask` with a mocked Ollama streams a non-empty response.

```bash
git commit -m "feat(web): add chat view with SSE streaming"
```

### Task 47: Settings view and manual PDF upload

**Files:**
- Create: `regwatch/web/routes/settings.py`
- Create: `regwatch/web/templates/settings.html`
- Create: `tests/integration/test_settings_view.py`

Routes:
- `GET /settings` — renders the current `AppConfig` read-only, lists the scheduler jobs with last-run status, shows Ollama health badge, lists `document_version` rows with `pdf_is_protected=True`, and the tail of `pipeline_run`.
- `POST /settings/run-job/{job_name}` — HTMX action that triggers the job immediately via `scheduler.get_job(job_name).modify(next_run_time=now)`.
- `POST /settings/upload-pdf/{version_id}` — multipart form upload. Saves the file under `paths.uploads_dir`, re-runs `extract_pdf` against it, updates the `document_version` row with `pdf_extracted_text`, `pdf_path`, `pdf_is_protected=False`, `pdf_manual_upload=True`. Re-runs `index_version` so the RAG catches the new content.

Test: a seeded protected document accepts an upload and ends up with extracted text.

```bash
git commit -m "feat(web): add settings view with manual PDF upload"
```

---

## Phase 9 — CLI completion

### Task 48: `run-pipeline`, `reindex`, `chat`, `dump-pipeline-runs`

**Files:**
- Modify: `regwatch/cli.py`
- Create: `tests/integration/test_cli_pipeline_commands.py`

Add four commands:

- `regwatch run-pipeline [--source NAME]` — builds the pipeline (via `pipeline_factory.build_runner`) with the configured sources and invokes `run_once()`. If `--source NAME` is given, only that source is activated for the run.
- `regwatch reindex` — iterates all `document_version` rows, clears the `document_chunk`, `document_chunk_vec`, and `document_chunk_fts` entries for each, and re-runs `index_version`. Useful when the embedding model changes.
- `regwatch chat "question"` — one-shot RAG: creates an ephemeral retrieval against default filters (in-force + both authorizations), calls `generate_answer`, prints the answer and cited chunk ids.
- `regwatch dump-pipeline-runs [--tail N]` — prints the last N `pipeline_run` rows as a formatted table (use `rich` if it's easy, or a plain typer echo otherwise).

Implementation sketch for `run-pipeline`:

```python
@app.command("run-pipeline")
def run_pipeline(
    source: Annotated[str | None, typer.Option("--source", "-s")] = None,
) -> None:
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    from sqlalchemy.orm import Session
    from regwatch.ollama.client import OllamaClient
    from regwatch.pipeline.fetch.base import REGISTRY
    # Import all source modules to register them.
    import regwatch.pipeline.fetch.cssf_rss  # noqa: F401
    import regwatch.pipeline.fetch.cssf_consultation  # noqa: F401
    import regwatch.pipeline.fetch.eur_lex_adopted  # noqa: F401
    import regwatch.pipeline.fetch.eur_lex_proposal  # noqa: F401
    import regwatch.pipeline.fetch.legilux_sparql  # noqa: F401
    import regwatch.pipeline.fetch.legilux_parliamentary  # noqa: F401
    import regwatch.pipeline.fetch.esma_rss  # noqa: F401
    import regwatch.pipeline.fetch.eba_rss  # noqa: F401
    import regwatch.pipeline.fetch.ec_fisma_rss  # noqa: F401
    from regwatch.pipeline.pipeline_factory import build_runner

    source_instances = []
    for name, source_cfg in cfg.sources.items():
        if not source_cfg.enabled:
            continue
        if source is not None and name != source:
            continue
        cls = REGISTRY[name]
        # Each source's constructor maps to its SourceConfig fields.
        if name == "cssf_rss":
            source_instances.append(cls(keywords=source_cfg.keywords))
        elif name == "eur_lex_adopted":
            source_instances.append(cls(celex_prefixes=source_cfg.celex_prefixes))
        elif name == "ec_fisma_rss":
            source_instances.append(cls(item_types=source_cfg.item_types, topic_ids=source_cfg.topic_ids))
        else:
            source_instances.append(cls())

    ollama = OllamaClient(
        base_url=cfg.ollama.base_url,
        chat_model=cfg.ollama.chat_model,
        embedding_model=cfg.ollama.embedding_model,
    )

    with Session(engine) as session:
        runner = build_runner(
            session,
            sources=source_instances,
            archive_root=cfg.paths.pdf_archive,
            ollama_client=ollama,
        )
        run_id = runner.run_once()
        session.commit()
    typer.echo(f"Pipeline run {run_id} completed.")
```

Tests use the same `_minimal_config` helper from Task 8, patch all sources to register a `FakeSource`, and assert that `run-pipeline` produces events.

Commit:
```bash
git commit -m "feat(cli): add run-pipeline, reindex, chat, dump-pipeline-runs"
```

---

## Final verification and documentation

### Task 49: Full test sweep and README polish

- [ ] **Step 1: Run the full unit + integration test suite**

```bash
pytest -v
```
Expected: all tests PASS.

- [ ] **Step 2: Run ruff and mypy**

```bash
ruff check .
mypy regwatch
```
Fix any remaining issues inline.

- [ ] **Step 3: Expand the README** with a usage section covering `init-db`, `seed`, `run-pipeline`, and starting the web server.

- [ ] **Step 4: Commit**

```bash
git commit -m "docs: polish README with usage examples"
```

---

## Notes on implementation discipline

- **Every task starts with a failing test.** Do not skip the "verify it fails" step; that step proves the test actually exercises the code under change.
- **Never mock the database.** All integration tests create a fresh `sqlite` file in `tmp_path`. Fast enough and catches real problems.
- **Mock only Ollama and outbound HTTP.** Use `pytest-httpx` for HTTP and `MagicMock` for `OllamaClient`.
- **Commit after every green test.** Small, focused commits make review and rollback trivial.
- **Follow the decisions in the spec.** When in doubt, re-read the relevant section of `docs/superpowers/specs/2026-04-08-regulatory-watcher-design.md`.

## Open points tracked from the spec

1. **Legilux parliamentary dossier SPARQL shape** (Task 22) — if SPARQL does not return draft bills, fall back to HTML scraping and document the decision in the module docstring.
2. **CSSF consultation coverage** (Task 24) — the title heuristic may miss items; log a TODO in the module and plan to revisit after first-week operation.
3. **Ollama model sizing** — the default is `llama3.1:8b`, configurable in `config.yaml`. If the target machine is CPU-only, swap in `qwen2.5:3b` by changing one line.
4. **Seed catalog completeness** (Task 7) — the committed seed covers ~10 representative regulations. Expand it to cover all regulations listed in the research section of the spec before running the first real pipeline.
5. **Automatic `regulation_lifecycle_link` creation** — the spec (section 4.4) calls for the pipeline to create links automatically when a new document is classified as `PROPOSAL` / `DRAFT_BILL` and references an in-force regulation. This is not yet wired into Phase 2 (the links can be created manually via a follow-up CLI command, or added to `persist_matched` in a later iteration). Track as a follow-up extension to Task 18: add a step that, when `matched.lifecycle_stage in {"PROPOSAL", "DRAFT_BILL"}` and a new `regulation` row with `source_of_truth="DISCOVERED"` is created, insert a `RegulationLifecycleLink` row for every `MatchedReference.regulation_id` with the appropriate `relation` (`PROPOSAL_OF` when the new stage is `PROPOSAL`, `TRANSPOSES` when `DRAFT_BILL`).
6. **"Discovered" regulation promotion flow** — the spec describes a UI action to promote or reject a newly discovered regulation. This is not in the Phase 8 UI tasks; add as a small follow-up after Task 43 (Catalog view) — a sub-tab under Catalog called "Discovered" with two buttons per row (Promote / Reject). The DB schema already supports this via `source_of_truth` and `lifecycle_stage`.

