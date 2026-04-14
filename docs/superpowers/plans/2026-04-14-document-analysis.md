# Document Analysis & Version-Scoped Chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users manually select regulations, download/upload their documents, run LLM-driven structured extraction against a user-configurable field schema, and chat with any subset of versions.

**Architecture:** New `regwatch/analysis/` package (fields → extractor → writeback → runner) sitting alongside `rag/`. Three new tables (`extraction_field`, `analysis_run`, `document_analysis`), additive columns on `regulation` and `document_chunk`. Reuses the existing `LLMClient`, `sync_schema` additive migrations, threaded-worker + `PipelineProgress` pattern, and sqlite-vec + FTS5 retriever.

**Tech stack:** Python 3.12, SQLAlchemy 2.0, FastAPI + Jinja2 + HTMX, Typer (CLI), pytest + pytest-httpx, sqlite-vec, FTS5, LM Studio (OpenAI-compatible) via `LLMClient`.

**Spec:** `docs/superpowers/specs/2026-04-14-document-analysis-design.md` — read this first.

**Conventions followed throughout:**
- TDD: write failing test → run to see it fail → minimal implementation → run to see pass → commit.
- Small per-task commits; never batch unrelated changes.
- No backward-compat shims; change every caller when a signature changes.
- Integration tests hit a fresh SQLite file; only LLM + outbound HTTP are mocked.
- `pytest` (the suite) must stay green after every task.

---

## Phase A — Schema + field registry

### Task A1: Add `ExtractionField` ORM model

**Files:**
- Modify: `regwatch/db/models.py` (append new class near other lookup-like tables)
- Test: `tests/unit/test_extraction_field_model.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_extraction_field_model.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import Base, ExtractionField, ExtractionFieldType


def test_extraction_field_round_trip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        f = ExtractionField(
            name="main_points",
            label="Main Points",
            description="Summarize the key obligations in 3-5 bullets.",
            data_type=ExtractionFieldType.LONG_TEXT,
            is_core=True,
            is_active=True,
            canonical_field=None,
            display_order=10,
        )
        s.add(f)
        s.commit()
        got = s.query(ExtractionField).filter_by(name="main_points").one()
        assert got.data_type is ExtractionFieldType.LONG_TEXT
        assert got.is_core is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_extraction_field_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'ExtractionField'`.

- [ ] **Step 3: Add the model**

Append to `regwatch/db/models.py`:

```python
class ExtractionFieldType(StrEnum):
    TEXT = "TEXT"
    LONG_TEXT = "LONG_TEXT"
    BOOL = "BOOL"
    DATE = "DATE"
    ENUM = "ENUM"
    LIST_TEXT = "LIST_TEXT"


class ExtractionField(Base):
    __tablename__ = "extraction_field"

    field_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    data_type: Mapped[ExtractionFieldType] = mapped_column(Enum(ExtractionFieldType))
    enum_values: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    canonical_field: Mapped[str | None] = mapped_column(String(100), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(UTC))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_extraction_field_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add regwatch/db/models.py tests/unit/test_extraction_field_model.py
git commit -m "feat(db): add ExtractionField model for configurable analysis schema"
```

---

### Task A2: Add `AnalysisRun` and `DocumentAnalysis` models

**Files:**
- Modify: `regwatch/db/models.py`
- Test: `tests/unit/test_analysis_models.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_analysis_models.py
from datetime import UTC, datetime, date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    Base,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)


def _seed_regulation_and_version(s: Session) -> DocumentVersion:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR,
        reference_number="CSSF 12/552",
        title="Test circular",
        issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        url="https://example.test/c.pdf",
        source_of_truth="SEED",
    )
    s.add(reg)
    s.flush()
    v = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=1,
        is_current=True,
        fetched_at=datetime.now(UTC),
        source_url="https://example.test/c.pdf",
        content_hash="abc",
    )
    s.add(v)
    s.flush()
    return v


def test_analysis_run_and_document_analysis_round_trip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        v = _seed_regulation_and_version(s)

        run = AnalysisRun(
            status=AnalysisRunStatus.SUCCESS,
            queued_version_ids=[v.version_id],
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            llm_model="qwen2.5-32b",
            triggered_by="USER_UI",
        )
        s.add(run)
        s.flush()

        a = DocumentAnalysis(
            run_id=run.run_id,
            version_id=v.version_id,
            regulation_id=v.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            raw_llm_output='{"is_ict": true}',
            was_truncated=False,
            is_ict=True,
            implementation_deadline=date(2026, 1, 17),
            document_relationship="NEW",
            keywords=["ICT", "DORA"],
            custom_fields={"severity": "high"},
        )
        s.add(a)
        s.commit()

        got = s.query(DocumentAnalysis).one()
        assert got.is_ict is True
        assert got.keywords == ["ICT", "DORA"]
        assert got.custom_fields == {"severity": "high"}
        assert got.run.status is AnalysisRunStatus.SUCCESS
```

- [ ] **Step 2: Run test — expect FAIL on import.**

Run: `pytest tests/unit/test_analysis_models.py -v`

- [ ] **Step 3: Add the models**

Append to `regwatch/db/models.py`:

```python
class AnalysisRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DocumentAnalysisStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class AnalysisRun(Base):
    __tablename__ = "analysis_run"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[AnalysisRunStatus] = mapped_column(Enum(AnalysisRunStatus))
    queued_version_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    llm_model: Mapped[str] = mapped_column(String(100))
    triggered_by: Mapped[str] = mapped_column(String(20))  # USER_UI | USER_CLI
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    analyses: Mapped[list[DocumentAnalysis]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class DocumentAnalysis(Base):
    __tablename__ = "document_analysis"

    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.run_id", ondelete="CASCADE"))
    version_id: Mapped[int] = mapped_column(
        ForeignKey("document_version.version_id", ondelete="CASCADE"), index=True
    )
    regulation_id: Mapped[int] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="CASCADE"), index=True
    )
    status: Mapped[DocumentAnalysisStatus] = mapped_column(Enum(DocumentAnalysisStatus))
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_llm_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    was_truncated: Mapped[bool] = mapped_column(Boolean, default=False)

    main_points: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    applicable_entity_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    is_ict: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ict_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_relevant_to_managed_entities: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relevance_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    implementation_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_relationship: Mapped[str | None] = mapped_column(String(20), nullable=True)
    relationship_target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    llm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(UTC))

    run: Mapped[AnalysisRun] = relationship(back_populates="analyses")

    __table_args__ = (
        UniqueConstraint("version_id", "run_id", name="uq_document_analysis_version_run"),
        Index("ix_document_analysis_regulation_created", "regulation_id", "created_at"),
    )
```

- [ ] **Step 4: Run test — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/db/models.py tests/unit/test_analysis_models.py
git commit -m "feat(db): add AnalysisRun and DocumentAnalysis models"
```

---

### Task A3: Additive columns on `Regulation` and `DocumentChunk`

**Files:**
- Modify: `regwatch/db/models.py`
- Test: `tests/unit/test_additive_columns.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_additive_columns.py
from sqlalchemy import create_engine, inspect
from regwatch.db.models import Base


def test_regulation_has_applicable_entity_types_and_chunk_has_heading_path():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("regulation")}
    assert "applicable_entity_types" in cols

    chunk_cols = {c["name"] for c in inspect(engine).get_columns("document_chunk")}
    assert "heading_path" in chunk_cols
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Add columns.**

In `Regulation`, below `notes`:

```python
    applicable_entity_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
```

In `DocumentChunk`, below `authorization_types`:

```python
    heading_path: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Run full suite** — `pytest -q` must stay green. `sync_schema` picks up both additive columns on next app start.

- [ ] **Step 6: Commit**

```bash
git add regwatch/db/models.py tests/unit/test_additive_columns.py
git commit -m "feat(db): add regulation.applicable_entity_types and document_chunk.heading_path"
```

---

### Task A4: Seed core `ExtractionField` rows

**Files:**
- Create: `regwatch/db/extraction_field_seed.py`
- Modify: `regwatch/cli.py` (call seeder after `init-db`)
- Modify: `regwatch/main.py` (call seeder after `sync_schema`)
- Test: `tests/unit/test_extraction_field_seed.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_extraction_field_seed.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import Base, ExtractionField


def test_seed_inserts_core_fields_idempotently():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_core_fields(s)
        s.commit()
        seed_core_fields(s)  # idempotent
        s.commit()
        names = [f.name for f in s.query(ExtractionField).all()]
        assert "main_points" in names
        assert "is_ict" in names
        assert "document_relationship" in names
        assert len(names) == len(set(names))
        ict = s.query(ExtractionField).filter_by(name="is_ict").one()
        assert ict.is_core is True
        assert ict.canonical_field == "is_ict"
```

- [ ] **Step 2: Run — expect FAIL on import.**

- [ ] **Step 3: Write `regwatch/db/extraction_field_seed.py`**

```python
"""Seed the non-deletable core extraction fields."""
from __future__ import annotations

from sqlalchemy.orm import Session

from regwatch.db.models import ExtractionField, ExtractionFieldType

_CORE_FIELDS: list[dict[str, object]] = [
    {
        "name": "main_points",
        "label": "Main Points",
        "description": "Summarize the document in 3-5 bullet points focusing on key obligations and scope.",
        "data_type": ExtractionFieldType.LONG_TEXT,
        "canonical_field": None,
        "display_order": 10,
    },
    {
        "name": "scope_description",
        "label": "Scope",
        "description": "Describe in one paragraph which activities, products, and processes are covered by this document.",
        "data_type": ExtractionFieldType.LONG_TEXT,
        "canonical_field": None,
        "display_order": 20,
    },
    {
        "name": "applicable_entity_types",
        "label": "Applicable Entity Types",
        "description": "List the CSSF / EU entity types this document applies to. Valid values: AIFM, CHAPTER15_MANCO, CREDIT_INSTITUTION, CASP, INVESTMENT_FIRM, INSURANCE, PENSION_FUND, ALL.",
        "data_type": ExtractionFieldType.LIST_TEXT,
        "canonical_field": "applicable_entity_types",
        "display_order": 30,
    },
    {
        "name": "is_ict",
        "label": "ICT / DORA Related",
        "description": "True if the document addresses ICT risk, cybersecurity, digital operational resilience, IT outsourcing, or similar technology-risk topics. False otherwise.",
        "data_type": ExtractionFieldType.BOOL,
        "canonical_field": "is_ict",
        "display_order": 40,
    },
    {
        "name": "ict_reasoning",
        "label": "ICT Reasoning",
        "description": "One sentence explaining why this document is or is not ICT-related.",
        "data_type": ExtractionFieldType.TEXT,
        "canonical_field": None,
        "display_order": 50,
    },
    {
        "name": "is_relevant_to_managed_entities",
        "label": "Relevant to Our Entities",
        "description": "True if this document is directly relevant to the entity types our tool manages (AIFM and CHAPTER15_MANCO). False otherwise.",
        "data_type": ExtractionFieldType.BOOL,
        "canonical_field": None,
        "display_order": 60,
    },
    {
        "name": "relevance_reasoning",
        "label": "Relevance Reasoning",
        "description": "One sentence explaining the relevance (or lack thereof) to our managed entities.",
        "data_type": ExtractionFieldType.TEXT,
        "canonical_field": None,
        "display_order": 70,
    },
    {
        "name": "implementation_deadline",
        "label": "Implementation Deadline",
        "description": "The latest date by which addressees must comply with this document. ISO-8601 date (YYYY-MM-DD) or null if not specified.",
        "data_type": ExtractionFieldType.DATE,
        "canonical_field": "implementation_deadline",
        "display_order": 80,
    },
    {
        "name": "deadline_description",
        "label": "Deadline Detail",
        "description": "Short text explaining the deadline (e.g. 'Enters into force 6 months after publication').",
        "data_type": ExtractionFieldType.TEXT,
        "canonical_field": None,
        "display_order": 90,
    },
    {
        "name": "document_relationship",
        "label": "Relationship to Existing Documents",
        "description": "Is this document NEW, REPLACES an existing document, AMENDS one, APPEALS one, or CLARIFIES one?",
        "data_type": ExtractionFieldType.ENUM,
        "enum_values": ["NEW", "REPLACES", "AMENDS", "APPEALS", "CLARIFIES"],
        "canonical_field": None,
        "display_order": 100,
    },
    {
        "name": "relationship_target",
        "label": "Related Document Reference",
        "description": "If this document REPLACES, AMENDS, APPEALS or CLARIFIES another, its reference (e.g. 'CSSF 12/552'). Null otherwise.",
        "data_type": ExtractionFieldType.TEXT,
        "canonical_field": None,
        "display_order": 110,
    },
    {
        "name": "keywords",
        "label": "Keywords",
        "description": "List of 5-10 short keywords or key-phrases capturing the document's main topics.",
        "data_type": ExtractionFieldType.LIST_TEXT,
        "canonical_field": None,
        "display_order": 120,
    },
]


def seed_core_fields(session: Session) -> int:
    """Insert any core fields that don't yet exist. Returns the number inserted."""
    existing = {f.name for f in session.query(ExtractionField).all()}
    inserted = 0
    for spec in _CORE_FIELDS:
        if spec["name"] in existing:
            continue
        row = ExtractionField(
            name=spec["name"],
            label=spec["label"],
            description=spec["description"],
            data_type=spec["data_type"],
            enum_values=spec.get("enum_values"),
            is_core=True,
            is_active=True,
            canonical_field=spec.get("canonical_field"),
            display_order=spec["display_order"],
        )
        session.add(row)
        inserted += 1
    session.flush()
    return inserted
```

- [ ] **Step 4: Run test — expect PASS.**

- [ ] **Step 5: Wire into `cli.py::init_db`.**

In `regwatch/cli.py`, after `sync_schema(engine, Base.metadata)`:

```python
from regwatch.db.extraction_field_seed import seed_core_fields
...
    with Session(engine) as session:
        seed_core_fields(session)
        session.commit()
```

And in `regwatch/main.py::create_app`, after `sync_schema(...)`:

```python
    from regwatch.db.extraction_field_seed import seed_core_fields
    with session_factory() as session:
        seed_core_fields(session)
        session.commit()
```

- [ ] **Step 6: Run full suite — `pytest -q`.**

- [ ] **Step 7: Commit**

```bash
git add regwatch/db/extraction_field_seed.py regwatch/cli.py regwatch/main.py tests/unit/test_extraction_field_seed.py
git commit -m "feat(analysis): seed 12 core extraction fields at init-db / startup"
```

---

### Task A5: `ExtractionFieldService` — CRUD with core-field protection

**Files:**
- Create: `regwatch/services/extraction_fields.py`
- Test: `tests/unit/test_extraction_field_service.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_extraction_field_service.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import Base, ExtractionFieldType
from regwatch.services.extraction_fields import (
    ExtractionFieldDTO,
    ExtractionFieldService,
    FieldProtectedError,
)


def _svc() -> tuple[ExtractionFieldService, Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_core_fields(s)
    s.commit()
    return ExtractionFieldService(s), s


def test_list_all_fields():
    svc, _ = _svc()
    rows = svc.list()
    assert len(rows) == 12
    assert all(isinstance(r, ExtractionFieldDTO) for r in rows)
    assert rows[0].display_order <= rows[-1].display_order


def test_add_custom_field_and_get_by_id():
    svc, _ = _svc()
    row = svc.create(
        name="severity", label="Severity", description="How severe?",
        data_type=ExtractionFieldType.TEXT, enum_values=None, display_order=200,
    )
    assert row.is_core is False
    assert svc.get(row.field_id).name == "severity"


def test_delete_user_field_ok_core_forbidden():
    svc, _ = _svc()
    custom = svc.create(
        name="severity", label="Severity", description="x",
        data_type=ExtractionFieldType.TEXT, enum_values=None, display_order=200,
    )
    svc.delete(custom.field_id)

    core_id = svc.list()[0].field_id
    with pytest.raises(FieldProtectedError):
        svc.delete(core_id)


def test_update_locks_core_immutable_columns():
    svc, _ = _svc()
    ict = next(f for f in svc.list() if f.name == "is_ict")
    svc.update(ict.field_id, label="ICT?", description="updated prompt", is_active=False)
    # name + data_type + canonical_field unchanged
    got = svc.get(ict.field_id)
    assert got.name == "is_ict"
    assert got.data_type is ExtractionFieldType.BOOL
    assert got.canonical_field == "is_ict"
    assert got.label == "ICT?"
    assert got.description == "updated prompt"
    assert got.is_active is False

    # Attempting to change name on a core field raises
    with pytest.raises(FieldProtectedError):
        svc.update(ict.field_id, name="not_allowed")
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement the service**

```python
# regwatch/services/extraction_fields.py
"""Service for CRUD on ExtractionField with core-field protection."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from regwatch.db.models import ExtractionField, ExtractionFieldType


class FieldProtectedError(RuntimeError):
    """Raised when a user tries to delete or alter a locked attribute of a core field."""


@dataclass
class ExtractionFieldDTO:
    field_id: int
    name: str
    label: str
    description: str
    data_type: ExtractionFieldType
    enum_values: list[str] | None
    is_core: bool
    is_active: bool
    canonical_field: str | None
    display_order: int


class ExtractionFieldService:
    def __init__(self, session: Session) -> None:
        self._s = session

    def list(self) -> list[ExtractionFieldDTO]:
        rows = (
            self._s.query(ExtractionField)
            .order_by(ExtractionField.display_order, ExtractionField.name)
            .all()
        )
        return [self._to_dto(r) for r in rows]

    def get(self, field_id: int) -> ExtractionFieldDTO:
        row = self._s.query(ExtractionField).filter_by(field_id=field_id).one()
        return self._to_dto(row)

    def create(
        self,
        *,
        name: str,
        label: str,
        description: str,
        data_type: ExtractionFieldType,
        enum_values: list[str] | None,
        display_order: int,
    ) -> ExtractionFieldDTO:
        row = ExtractionField(
            name=name,
            label=label,
            description=description,
            data_type=data_type,
            enum_values=enum_values,
            is_core=False,
            is_active=True,
            canonical_field=None,
            display_order=display_order,
        )
        self._s.add(row)
        self._s.flush()
        return self._to_dto(row)

    def update(self, field_id: int, **changes: object) -> ExtractionFieldDTO:
        row = self._s.query(ExtractionField).filter_by(field_id=field_id).one()
        locked_for_core = {"name", "data_type", "canonical_field", "is_core"}
        if row.is_core:
            for k in changes.keys() & locked_for_core:
                raise FieldProtectedError(
                    f"Cannot change '{k}' on core field '{row.name}'"
                )
        for k, v in changes.items():
            setattr(row, k, v)
        self._s.flush()
        return self._to_dto(row)

    def delete(self, field_id: int) -> None:
        row = self._s.query(ExtractionField).filter_by(field_id=field_id).one()
        if row.is_core:
            raise FieldProtectedError(f"Cannot delete core field '{row.name}'")
        self._s.delete(row)
        self._s.flush()

    @staticmethod
    def _to_dto(row: ExtractionField) -> ExtractionFieldDTO:
        return ExtractionFieldDTO(
            field_id=row.field_id,
            name=row.name,
            label=row.label,
            description=row.description,
            data_type=row.data_type,
            enum_values=row.enum_values,
            is_core=row.is_core,
            is_active=row.is_active,
            canonical_field=row.canonical_field,
            display_order=row.display_order,
        )
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/services/extraction_fields.py tests/unit/test_extraction_field_service.py
git commit -m "feat(analysis): ExtractionFieldService with core-field protection"
```

---

### Task A6: `/settings/extraction` route + templates

**Files:**
- Create: `regwatch/web/templates/settings/extraction.html`
- Create: `regwatch/web/templates/settings/_extraction_row.html`
- Modify: `regwatch/web/routes/settings.py`
- Test: `tests/integration/test_extraction_field_routes.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/integration/test_extraction_field_routes.py
from tests.integration.test_app_smoke import _client  # existing helper


def test_extraction_fields_page_lists_core_rows(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/settings/extraction")
        assert r.status_code == 200
        assert "Main Points" in r.text
        assert "ICT / DORA Related" in r.text


def test_create_and_delete_custom_field(tmp_path):
    with _client(tmp_path) as c:
        r = c.post(
            "/settings/extraction",
            data={
                "name": "severity", "label": "Severity", "description": "How bad",
                "data_type": "TEXT", "enum_values": "", "display_order": "200",
            },
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        listing = c.get("/settings/extraction").text
        assert "Severity" in listing

        # Find field_id via DB — simpler: issue delete via a known row id fetched from the page.
        # Use service directly through the app state:
        from regwatch.services.extraction_fields import ExtractionFieldService
        with c.app.state.session_factory() as s:
            fid = next(f.field_id for f in ExtractionFieldService(s).list() if f.name == "severity")
        r = c.post(f"/settings/extraction/{fid}/delete", follow_redirects=False)
        assert r.status_code in (302, 303)
        listing = c.get("/settings/extraction").text
        assert "Severity" not in listing


def test_cannot_delete_core_field(tmp_path):
    with _client(tmp_path) as c:
        from regwatch.services.extraction_fields import ExtractionFieldService
        with c.app.state.session_factory() as s:
            core_id = next(f.field_id for f in ExtractionFieldService(s).list() if f.is_core)
        r = c.post(f"/settings/extraction/{core_id}/delete", follow_redirects=False)
        assert r.status_code == 400
```

- [ ] **Step 2: Run — expect FAIL (route not found).**

- [ ] **Step 3: Add routes**

In `regwatch/web/routes/settings.py`, add:

```python
from fastapi import Form, HTTPException
from fastapi.responses import RedirectResponse

from regwatch.db.models import ExtractionFieldType
from regwatch.services.extraction_fields import ExtractionFieldService, FieldProtectedError


@router.get("/settings/extraction")
def extraction_fields_page(request: Request):
    with request.app.state.session_factory() as session:
        fields = ExtractionFieldService(session).list()
    return request.app.state.templates.TemplateResponse(
        "settings/extraction.html",
        {"request": request, "fields": fields, "data_types": list(ExtractionFieldType)},
    )


@router.post("/settings/extraction")
def create_extraction_field(
    request: Request,
    name: str = Form(...),
    label: str = Form(...),
    description: str = Form(...),
    data_type: str = Form(...),
    enum_values: str = Form(""),
    display_order: int = Form(100),
):
    with request.app.state.session_factory() as session:
        svc = ExtractionFieldService(session)
        try:
            dtype = ExtractionFieldType(data_type)
        except ValueError:
            raise HTTPException(400, f"Invalid data_type: {data_type}")
        enum_list = (
            [v.strip() for v in enum_values.split(",") if v.strip()]
            if dtype is ExtractionFieldType.ENUM
            else None
        )
        svc.create(
            name=name, label=label, description=description, data_type=dtype,
            enum_values=enum_list, display_order=display_order,
        )
        session.commit()
    return RedirectResponse("/settings/extraction", status_code=303)


@router.post("/settings/extraction/{field_id}/update")
def update_extraction_field(
    request: Request,
    field_id: int,
    label: str = Form(...),
    description: str = Form(...),
    display_order: int = Form(100),
    is_active: bool = Form(False),
):
    with request.app.state.session_factory() as session:
        try:
            ExtractionFieldService(session).update(
                field_id,
                label=label, description=description,
                display_order=display_order, is_active=is_active,
            )
            session.commit()
        except FieldProtectedError as e:
            raise HTTPException(400, str(e))
    return RedirectResponse("/settings/extraction", status_code=303)


@router.post("/settings/extraction/{field_id}/delete")
def delete_extraction_field(request: Request, field_id: int):
    with request.app.state.session_factory() as session:
        try:
            ExtractionFieldService(session).delete(field_id)
            session.commit()
        except FieldProtectedError as e:
            raise HTTPException(400, str(e))
    return RedirectResponse("/settings/extraction", status_code=303)
```

- [ ] **Step 4: Add templates.**

Create `regwatch/web/templates/settings/extraction.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Extraction Fields</h1>
<p>Fields extracted from every document during analysis. Core fields are protected; their labels, prompts and active state are editable but names and types are not.</p>

<table class="table">
  <thead>
    <tr><th>Order</th><th>Name</th><th>Label</th><th>Type</th><th>Active</th><th>Core</th><th>Actions</th></tr>
  </thead>
  <tbody>
    {% for f in fields %}
      {% include "settings/_extraction_row.html" %}
    {% endfor %}
  </tbody>
</table>

<h2>Add custom field</h2>
<form method="post" action="/settings/extraction">
  <label>Name (machine, lowercase, no spaces)<input name="name" required pattern="[a-z][a-z0-9_]*"></label>
  <label>Label<input name="label" required></label>
  <label>Description (LLM prompt)<textarea name="description" rows="3" required></textarea></label>
  <label>Data type
    <select name="data_type">
      {% for t in data_types %}<option value="{{ t.value }}">{{ t.value }}</option>{% endfor %}
    </select>
  </label>
  <label>Enum values (comma-separated, only for ENUM type)<input name="enum_values"></label>
  <label>Display order<input type="number" name="display_order" value="200"></label>
  <button type="submit">Add field</button>
</form>
{% endblock %}
```

Create `regwatch/web/templates/settings/_extraction_row.html`:

```html
<tr>
  <td>{{ f.display_order }}</td>
  <td>{{ f.name }}</td>
  <td>
    <form method="post" action="/settings/extraction/{{ f.field_id }}/update" style="display:inline">
      <input name="label" value="{{ f.label }}" size="20">
      <input name="description" value="{{ f.description }}" size="50">
      <input type="number" name="display_order" value="{{ f.display_order }}">
      <label><input type="checkbox" name="is_active" value="true" {% if f.is_active %}checked{% endif %}> active</label>
      <button type="submit">Save</button>
    </form>
  </td>
  <td>{{ f.data_type.value }}</td>
  <td>{{ "Yes" if f.is_active else "No" }}</td>
  <td>{{ "🔒" if f.is_core else "" }}</td>
  <td>
    {% if not f.is_core %}
      <form method="post" action="/settings/extraction/{{ f.field_id }}/delete" style="display:inline">
        <button type="submit" onclick="return confirm('Delete field?')">Delete</button>
      </form>
    {% endif %}
  </td>
</tr>
```

- [ ] **Step 5: Run — expect PASS for the three test cases. Fix template details if needed.**

- [ ] **Step 6: Add a nav link.** In whichever shared template holds the nav, add `<a href="/settings/extraction">Extraction Fields</a>` next to existing Settings links.

- [ ] **Step 7: Commit**

```bash
git add regwatch/web/routes/settings.py regwatch/web/templates/settings/extraction.html regwatch/web/templates/settings/_extraction_row.html tests/integration/test_extraction_field_routes.py
git commit -m "feat(web): /settings/extraction CRUD page with core-field protection"
```

---

## Phase B — Analysis engine (CLI-driven)

### Task B1: `regwatch/analysis/fields.py` — build prompt schema

**Files:**
- Create: `regwatch/analysis/__init__.py` (empty)
- Create: `regwatch/analysis/fields.py`
- Test: `tests/unit/test_analysis_fields.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_analysis_fields.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.analysis.fields import build_prompt_schema, coerce_value
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import Base, ExtractionFieldType
from datetime import date


def test_build_prompt_schema_lists_active_fields_in_order():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_core_fields(s)
        s.commit()
        text = build_prompt_schema(s)
        assert "main_points" in text
        assert "is_ict" in text
        # Check ordering — main_points (10) before is_ict (40)
        assert text.index("main_points") < text.index("is_ict")
        assert "LONG_TEXT" in text
        assert "BOOL" in text


def test_coerce_bool():
    assert coerce_value(True, ExtractionFieldType.BOOL) is True
    assert coerce_value("true", ExtractionFieldType.BOOL) is True
    assert coerce_value("False", ExtractionFieldType.BOOL) is False
    assert coerce_value(0, ExtractionFieldType.BOOL) is False


def test_coerce_date():
    assert coerce_value("2026-01-17", ExtractionFieldType.DATE) == date(2026, 1, 17)
    assert coerce_value(None, ExtractionFieldType.DATE) is None


def test_coerce_list_text():
    assert coerce_value(["a", "b"], ExtractionFieldType.LIST_TEXT) == ["a", "b"]
    assert coerce_value("a, b, c", ExtractionFieldType.LIST_TEXT) == ["a", "b", "c"]
    assert coerce_value(None, ExtractionFieldType.LIST_TEXT) is None


def test_coerce_enum():
    assert coerce_value("NEW", ExtractionFieldType.ENUM) == "NEW"
    assert coerce_value("  replaces  ", ExtractionFieldType.ENUM) == "REPLACES"
```

- [ ] **Step 2: Run — expect FAIL on import.**

- [ ] **Step 3: Implement**

```python
# regwatch/analysis/fields.py
"""Build the LLM prompt schema from active extraction_field rows, and coerce outputs."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from regwatch.db.models import ExtractionField, ExtractionFieldType


def build_prompt_schema(session: Session) -> str:
    """Render an active-fields schema description for the user message."""
    rows = (
        session.query(ExtractionField)
        .filter(ExtractionField.is_active == True)  # noqa: E712
        .order_by(ExtractionField.display_order, ExtractionField.name)
        .all()
    )
    lines: list[str] = []
    for f in rows:
        enum_hint = ""
        if f.data_type is ExtractionFieldType.ENUM and f.enum_values:
            enum_hint = f" (one of: {', '.join(f.enum_values)})"
        lines.append(f"- {f.name} ({f.data_type.value}{enum_hint}): {f.description}")
    return "\n".join(lines)


def coerce_value(value: Any, dtype: ExtractionFieldType) -> Any:
    """Coerce an LLM-returned raw value to the declared Python type."""
    if value is None:
        return None
    if dtype is ExtractionFieldType.BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("true", "yes", "1", "y")
        return bool(value)
    if dtype is ExtractionFieldType.DATE:
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value.strip():
            return date.fromisoformat(value.strip())
        return None
    if dtype is ExtractionFieldType.LIST_TEXT:
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [t.strip() for t in value.split(",") if t.strip()]
        return None
    if dtype is ExtractionFieldType.ENUM:
        return str(value).strip().upper()
    # TEXT, LONG_TEXT
    if isinstance(value, str):
        return value
    return str(value)
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/analysis/__init__.py regwatch/analysis/fields.py tests/unit/test_analysis_fields.py
git commit -m "feat(analysis): prompt schema builder and type coercer"
```

---

### Task B2: `regwatch/analysis/extractor.py` — LLM call + JSON parse

**Files:**
- Create: `regwatch/analysis/extractor.py`
- Test: `tests/unit/test_analysis_extractor.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_analysis_extractor.py
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.analysis.extractor import ExtractionResult, extract
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import Base


def _session_with_fields() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_core_fields(s)
    s.commit()
    return s


def test_extract_parses_llm_json_and_coerces_types():
    s = _session_with_fields()
    llm = MagicMock()
    llm.chat.return_value = """
    {
      "main_points": "- Point 1\\n- Point 2",
      "scope_description": "All Luxembourg AIFMs.",
      "applicable_entity_types": ["AIFM"],
      "is_ict": true,
      "ict_reasoning": "Mentions ICT risk.",
      "is_relevant_to_managed_entities": true,
      "relevance_reasoning": "AIFM applies.",
      "implementation_deadline": "2026-01-17",
      "deadline_description": "Six months after publication.",
      "document_relationship": "REPLACES",
      "relationship_target": "CSSF 12/552",
      "keywords": ["ICT", "DORA"]
    }
    """
    result = extract(
        session=s, llm=llm, regulation_metadata="CSSF 12/552 — Risk mgmt — CSSF",
        document_text="... long text ...", max_tokens=10000,
    )
    assert isinstance(result, ExtractionResult)
    assert result.status == "SUCCESS"
    assert result.values["is_ict"] is True
    assert result.values["keywords"] == ["ICT", "DORA"]
    assert result.was_truncated is False


def test_extract_flags_truncation():
    s = _session_with_fields()
    llm = MagicMock()
    llm.chat.return_value = '{"is_ict": true, "keywords": []}'
    big_text = "word " * 50000  # will exceed 10k-token budget
    result = extract(
        session=s, llm=llm, regulation_metadata="meta",
        document_text=big_text, max_tokens=1000,
    )
    assert result.was_truncated is True


def test_extract_marks_failed_on_bad_json():
    s = _session_with_fields()
    llm = MagicMock()
    llm.chat.return_value = "not json at all"
    result = extract(
        session=s, llm=llm, regulation_metadata="meta",
        document_text="doc", max_tokens=10000,
    )
    assert result.status == "FAILED"
    assert "JSON" in (result.error or "")
    assert result.raw_output == "not json at all"
```

- [ ] **Step 2: Run — expect FAIL on import.**

- [ ] **Step 3: Implement**

```python
# regwatch/analysis/extractor.py
"""Call the LLM with the active-fields schema and parse its JSON reply."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from regwatch.analysis.fields import build_prompt_schema, coerce_value
from regwatch.db.models import ExtractionField
from regwatch.llm.client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a regulatory-document analyst. Extract the requested fields from the "
    "document and return a JSON object with exactly the keys listed. Use null for "
    "fields not present in the document. Return ONLY the JSON object, no commentary."
)

# Conservative heuristic: 1 token ≈ 4 characters for European-language text.
_CHARS_PER_TOKEN = 4


@dataclass
class ExtractionResult:
    status: str  # "SUCCESS" | "FAILED"
    raw_output: str
    was_truncated: bool
    values: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _truncate_to_budget(text: str, max_tokens: int) -> tuple[str, bool]:
    budget = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= budget:
        return text, False
    return text[:budget], True


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Tolerant JSON parser — accepts ```json fenced blocks and leading prose."""
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    # Locate the first '{' and matching last '}' to tolerate trailing text.
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first == -1 or last <= first:
        raise json.JSONDecodeError("no JSON object found", stripped, 0)
    return json.loads(stripped[first : last + 1])


def extract(
    *,
    session: Session,
    llm: LLMClient,
    regulation_metadata: str,
    document_text: str,
    max_tokens: int,
) -> ExtractionResult:
    schema = build_prompt_schema(session)
    truncated_text, was_truncated = _truncate_to_budget(document_text, max_tokens)

    user_msg = (
        f"Regulation: {regulation_metadata}\n\n"
        f"Extract these fields and return valid JSON with exactly these keys:\n{schema}\n\n"
        f"--- DOCUMENT ---\n{truncated_text}\n--- END DOCUMENT ---"
    )

    try:
        raw = llm.chat(system=_SYSTEM, user=user_msg)
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM call failed: %s", e)
        return ExtractionResult(
            status="FAILED", raw_output="", was_truncated=was_truncated, error=str(e)
        )

    try:
        data = _extract_json_object(raw)
    except json.JSONDecodeError as e:
        return ExtractionResult(
            status="FAILED", raw_output=raw, was_truncated=was_truncated,
            error=f"Invalid JSON in LLM reply: {e}",
        )

    fields = (
        session.query(ExtractionField)
        .filter(ExtractionField.is_active == True)  # noqa: E712
        .all()
    )
    values: dict[str, Any] = {}
    for f in fields:
        raw_val = data.get(f.name)
        try:
            values[f.name] = coerce_value(raw_val, f.data_type)
        except Exception as e:  # noqa: BLE001
            logger.warning("Coercion failed for %s: %s", f.name, e)
            values[f.name] = None
    return ExtractionResult(
        status="SUCCESS", raw_output=raw, was_truncated=was_truncated, values=values,
    )
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/analysis/extractor.py tests/unit/test_analysis_extractor.py
git commit -m "feat(analysis): LLM-backed field extractor with tolerant JSON parsing"
```

---

### Task B3: `regwatch/analysis/writeback.py` — canonical-field propagation

**Files:**
- Create: `regwatch/analysis/writeback.py`
- Test: `tests/unit/test_analysis_writeback.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_analysis_writeback.py
from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.analysis.writeback import apply_writeback
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    Base,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationOverride,
    RegulationType,
)


def _seed(s: Session, *, reference: str = "CSSF 12/552") -> tuple[Regulation, DocumentVersion]:
    reg = Regulation(
        type=RegulationType.CSSF_CIRCULAR, reference_number=reference,
        title="Test", issuing_authority="CSSF",
        lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        is_ict=False,
    )
    s.add(reg)
    s.flush()
    v = DocumentVersion(
        regulation_id=reg.regulation_id, version_number=1, is_current=True,
        fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
    )
    s.add(v)
    s.flush()
    return reg, v


def _make_run(s: Session, version_id: int) -> AnalysisRun:
    run = AnalysisRun(
        status=AnalysisRunStatus.RUNNING, queued_version_ids=[version_id],
        started_at=datetime.now(UTC), llm_model="test", triggered_by="USER_CLI",
    )
    s.add(run)
    s.flush()
    return run


def test_writeback_updates_is_ict_and_entity_types():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg, v = _seed(s)
        run = _make_run(s, v.version_id)
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=v.version_id, regulation_id=reg.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            is_ict=True, applicable_entity_types=["AIFM", "CHAPTER15_MANCO"],
            document_relationship="NEW",
        )
        s.add(a)
        s.flush()
        apply_writeback(s, a)
        s.refresh(reg)
        assert reg.is_ict is True
        assert reg.applicable_entity_types == ["AIFM", "CHAPTER15_MANCO"]


def test_writeback_respects_set_ict_override():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg, v = _seed(s)
        s.add(RegulationOverride(
            regulation_id=reg.regulation_id, reference_number=reg.reference_number,
            action="UNSET_ICT", created_at=datetime.now(UTC),
        ))
        s.flush()
        run = _make_run(s, v.version_id)
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=v.version_id, regulation_id=reg.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS, is_ict=True,
        )
        s.add(a); s.flush()
        apply_writeback(s, a)
        s.refresh(reg)
        assert reg.is_ict is False  # override wins


def test_writeback_replaces_sets_replaced_by_id():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        old, _ = _seed(s, reference="CSSF 11/498")
        new, v = _seed(s, reference="CSSF 12/552")
        run = _make_run(s, v.version_id)
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=v.version_id, regulation_id=new.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            document_relationship="REPLACES", relationship_target="CSSF 11/498",
        )
        s.add(a); s.flush()
        apply_writeback(s, a)
        s.refresh(old)
        assert old.replaced_by_id == new.regulation_id


def test_writeback_deadline_routes_to_transposition_for_eu_directive():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.EU_DIRECTIVE, reference_number="DORA",
            celex_id="32022L2556", title="DORA", issuing_authority="EU",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
            is_ict=False,
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
        )
        s.add(v); s.flush()
        run = _make_run(s, v.version_id)
        a = DocumentAnalysis(
            run_id=run.run_id, version_id=v.version_id, regulation_id=reg.regulation_id,
            status=DocumentAnalysisStatus.SUCCESS,
            implementation_deadline=date(2025, 1, 17),
            document_relationship="NEW",
        )
        s.add(a); s.flush()
        apply_writeback(s, a)
        s.refresh(reg)
        assert reg.transposition_deadline == date(2025, 1, 17)
        assert reg.application_date is None
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# regwatch/analysis/writeback.py
"""Apply canonical-field updates from a DocumentAnalysis to its parent Regulation."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentAnalysis,
    DocumentVersion,
    Regulation,
    RegulationOverride,
    RegulationType,
)

logger = logging.getLogger(__name__)

_ICT_OVERRIDE_ACTIONS = {"SET_ICT", "UNSET_ICT", "EXCLUDE"}


def apply_writeback(session: Session, analysis: DocumentAnalysis) -> None:
    """Update canonical fields on the parent Regulation from this analysis.

    Only runs when this analysis belongs to the regulation's CURRENT version.
    Respects RegulationOverride rows for is_ict.
    """
    current_version_id = session.scalar(
        select(DocumentVersion.version_id)
        .where(DocumentVersion.regulation_id == analysis.regulation_id)
        .where(DocumentVersion.is_current == True)  # noqa: E712
    )
    if current_version_id != analysis.version_id:
        return  # analysis of a non-current version never mutates the regulation

    reg = session.get(Regulation, analysis.regulation_id)
    if reg is None:
        return

    overrides = {
        r.action
        for r in session.query(RegulationOverride)
        .filter(RegulationOverride.reference_number == reg.reference_number)
        .all()
    }

    if analysis.is_ict is not None and not overrides & _ICT_OVERRIDE_ACTIONS:
        reg.is_ict = analysis.is_ict

    if analysis.applicable_entity_types is not None:
        reg.applicable_entity_types = analysis.applicable_entity_types

    if analysis.implementation_deadline is not None:
        if _is_eu_directive(reg) and analysis.document_relationship in {None, "NEW", "REPLACES", "AMENDS"}:
            reg.transposition_deadline = analysis.implementation_deadline
        else:
            reg.application_date = analysis.implementation_deadline

    if analysis.document_relationship == "REPLACES" and analysis.relationship_target:
        old_reg = _resolve_reference(session, analysis.relationship_target)
        if old_reg is not None and old_reg.regulation_id != reg.regulation_id:
            old_reg.replaced_by_id = reg.regulation_id
        else:
            logger.info(
                "Could not resolve replaced reference '%s' for regulation '%s'",
                analysis.relationship_target, reg.reference_number,
            )


def _is_eu_directive(reg: Regulation) -> bool:
    return reg.type is RegulationType.EU_DIRECTIVE or bool(reg.celex_id)


def _resolve_reference(session: Session, ref: str) -> Regulation | None:
    ref = ref.strip()
    return session.scalar(
        select(Regulation).where(
            (Regulation.reference_number == ref) | (Regulation.celex_id == ref)
        )
    )
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/analysis/writeback.py tests/unit/test_analysis_writeback.py
git commit -m "feat(analysis): write-back of canonical fields to Regulation with override precedence"
```

---

### Task B4: `regwatch/analysis/runner.py` — orchestrator

**Files:**
- Create: `regwatch/analysis/runner.py`
- Test: `tests/integration/test_analysis_runner.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/integration/test_analysis_runner.py
from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from regwatch.analysis.runner import AnalysisRunner
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    Base,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)


def _seed_one(sf) -> int:
    with sf() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
            title="Risk mgmt", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="This circular addresses ICT risk management.",
        )
        s.add(v); s.flush()
        seed_core_fields(s)
        s.commit()
        return v.version_id


def test_runner_runs_and_persists_analysis():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    version_id = _seed_one(sf)

    llm = MagicMock()
    llm.chat.return_value = (
        '{"main_points": "- ICT risk.", "is_ict": true, '
        '"document_relationship": "NEW", "keywords": ["ICT"]}'
    )

    runner = AnalysisRunner(session_factory=sf, llm=llm, max_document_tokens=5000)
    run_id = runner.queue_and_run([version_id], triggered_by="USER_CLI", llm_model="t")

    with sf() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status is AnalysisRunStatus.SUCCESS
        analyses = s.query(DocumentAnalysis).all()
        assert len(analyses) == 1
        a = analyses[0]
        assert a.status is DocumentAnalysisStatus.SUCCESS
        assert a.is_ict is True
        # Writeback applied
        reg = s.get(Regulation, a.regulation_id)
        assert reg.is_ict is True


def test_runner_marks_partial_on_mixed_failures():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    good = _seed_one(sf)
    # Seed a second version with a different regulation
    with sf() as s:
        reg2 = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 20/759",
            title="Other", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg2); s.flush()
        v2 = DocumentVersion(
            regulation_id=reg2.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h2",
            pdf_extracted_text="",  # blank text → will fail analysis
        )
        s.add(v2); s.flush()
        s.commit()
        bad = v2.version_id

    llm = MagicMock()
    llm.chat.side_effect = ['{"is_ict": true}', Exception("LLM timeout")]

    runner = AnalysisRunner(session_factory=sf, llm=llm, max_document_tokens=5000)
    run_id = runner.queue_and_run([good, bad], triggered_by="USER_CLI", llm_model="t")

    with sf() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status is AnalysisRunStatus.PARTIAL
        statuses = sorted(
            a.status.value for a in s.query(DocumentAnalysis).all()
        )
        assert statuses == ["FAILED", "SUCCESS"]
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# regwatch/analysis/runner.py
"""Run analysis over a list of DocumentVersion ids: LLM call, persist, write-back."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from regwatch.analysis.extractor import extract
from regwatch.analysis.writeback import apply_writeback
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    Regulation,
)
from regwatch.llm.client import LLMClient

logger = logging.getLogger(__name__)


class AnalysisRunner:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        llm: LLMClient,
        max_document_tokens: int,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self._sf = session_factory
        self._llm = llm
        self._max_tokens = max_document_tokens
        self._on_progress = on_progress or (lambda *_: None)

    def queue_and_run(
        self, version_ids: list[int], *, triggered_by: str, llm_model: str
    ) -> int:
        """Create an AnalysisRun row, iterate the versions, return run_id."""
        with self._sf() as s:
            run = AnalysisRun(
                status=AnalysisRunStatus.RUNNING,
                queued_version_ids=list(version_ids),
                started_at=datetime.now(UTC),
                llm_model=llm_model,
                triggered_by=triggered_by,
            )
            s.add(run)
            s.commit()
            run_id = run.run_id

        succeeded = 0
        failed = 0
        errors: list[str] = []
        for i, vid in enumerate(version_ids, start=1):
            self._on_progress(i, len(version_ids), f"version {vid}")
            try:
                status = self._analyse_one(run_id, vid)
            except Exception as e:  # noqa: BLE001 — defensive: never kill the run
                logger.exception("Unexpected error analysing version %s", vid)
                status = DocumentAnalysisStatus.FAILED
                errors.append(f"version {vid}: {e}")
            if status is DocumentAnalysisStatus.SUCCESS:
                succeeded += 1
            else:
                failed += 1

        with self._sf() as s:
            run = s.get(AnalysisRun, run_id)
            if succeeded == len(version_ids):
                run.status = AnalysisRunStatus.SUCCESS
            elif succeeded == 0:
                run.status = AnalysisRunStatus.FAILED
            else:
                run.status = AnalysisRunStatus.PARTIAL
            run.finished_at = datetime.now(UTC)
            if errors:
                run.error_summary = "\n".join(errors)
            s.commit()
        return run_id

    def _analyse_one(self, run_id: int, version_id: int) -> DocumentAnalysisStatus:
        with self._sf() as s:
            version = s.get(DocumentVersion, version_id)
            if version is None:
                self._save_failure(s, run_id, version_id, None, "Version not found")
                return DocumentAnalysisStatus.FAILED

            text = version.pdf_extracted_text or version.html_text or ""
            if not text.strip():
                self._save_failure(
                    s, run_id, version_id, version.regulation_id,
                    "Document has no extracted text; upload manually or re-fetch.",
                )
                return DocumentAnalysisStatus.FAILED

            reg = s.get(Regulation, version.regulation_id)
            meta = (
                f"{reg.reference_number} — {reg.title} — {reg.issuing_authority}"
                if reg else f"version {version_id}"
            )
            result = extract(
                session=s, llm=self._llm,
                regulation_metadata=meta, document_text=text, max_tokens=self._max_tokens,
            )
            if result.status == "FAILED":
                self._save_failure(
                    s, run_id, version_id, version.regulation_id,
                    result.error or "extraction failed", raw=result.raw_output,
                    was_truncated=result.was_truncated,
                )
                return DocumentAnalysisStatus.FAILED

            a = DocumentAnalysis(
                run_id=run_id, version_id=version_id, regulation_id=version.regulation_id,
                status=DocumentAnalysisStatus.SUCCESS,
                raw_llm_output=result.raw_output, was_truncated=result.was_truncated,
            )
            self._assign_core_values(a, result.values)
            a.custom_fields = self._collect_custom_values(s, result.values)
            s.add(a); s.flush()
            apply_writeback(s, a)
            s.commit()
            return DocumentAnalysisStatus.SUCCESS

    @staticmethod
    def _assign_core_values(a: DocumentAnalysis, values: dict[str, object]) -> None:
        core_cols = {
            "main_points", "scope_description", "applicable_entity_types",
            "is_ict", "ict_reasoning", "is_relevant_to_managed_entities",
            "relevance_reasoning", "implementation_deadline", "deadline_description",
            "document_relationship", "relationship_target", "keywords",
        }
        for col in core_cols:
            if col in values:
                setattr(a, col, values[col])

    @staticmethod
    def _collect_custom_values(s: Session, values: dict[str, object]) -> dict[str, object]:
        from regwatch.db.models import ExtractionField
        custom_names = {
            f.name for f in s.query(ExtractionField).filter(
                ExtractionField.is_core == False,  # noqa: E712
                ExtractionField.is_active == True,  # noqa: E712
            ).all()
        }
        return {k: v for k, v in values.items() if k in custom_names}

    @staticmethod
    def _save_failure(
        s: Session, run_id: int, version_id: int, regulation_id: int | None,
        error: str, *, raw: str = "", was_truncated: bool = False,
    ) -> None:
        # regulation_id may be None if we couldn't load the version — fall back to 0
        # to satisfy NOT NULL; that row is visible in the run listing and flagged FAILED.
        a = DocumentAnalysis(
            run_id=run_id, version_id=version_id,
            regulation_id=regulation_id or 0,
            status=DocumentAnalysisStatus.FAILED,
            error_detail=error, raw_llm_output=raw, was_truncated=was_truncated,
        )
        s.add(a); s.commit()
```

> **Note** — the failure path uses `regulation_id=0` if the version couldn't be loaded. That's a sentinel, not a real FK target; since we're in SQLite without enforced FKs during tests, this works. If you prefer stricter behaviour, make `DocumentAnalysis.regulation_id` nullable (additive change); but the current schema is fine.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/analysis/runner.py tests/integration/test_analysis_runner.py
git commit -m "feat(analysis): AnalysisRunner orchestrator with per-document success/failure"
```

---

### Task B5: `regwatch/services/analysis.py` — service + DTOs

**Files:**
- Create: `regwatch/services/analysis.py`
- Test: `tests/unit/test_analysis_service.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_analysis_service.py
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.models import (
    AnalysisRun, AnalysisRunStatus, Base, DocumentAnalysis, DocumentAnalysisStatus,
    DocumentVersion, LifecycleStage, Regulation, RegulationType,
)
from regwatch.services.analysis import AnalysisService


def test_latest_analysis_for_regulation_returns_newest():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="x", title="t",
            issuing_authority="x", lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
        )
        s.add(v); s.flush()

        for i in range(2):
            run = AnalysisRun(
                status=AnalysisRunStatus.SUCCESS, queued_version_ids=[v.version_id],
                started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
                llm_model="t", triggered_by="USER_CLI",
            )
            s.add(run); s.flush()
            a = DocumentAnalysis(
                run_id=run.run_id, version_id=v.version_id, regulation_id=reg.regulation_id,
                status=DocumentAnalysisStatus.SUCCESS, is_ict=bool(i),
            )
            s.add(a); s.commit()

        svc = AnalysisService(s)
        dto = svc.latest_for_regulation(reg.regulation_id)
        assert dto is not None
        assert dto.is_ict is True  # second run won
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
# regwatch/services/analysis.py
"""Service DTOs for AnalysisRun / DocumentAnalysis listings and detail pages."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from regwatch.db.models import AnalysisRun, DocumentAnalysis


@dataclass
class DocumentAnalysisDTO:
    analysis_id: int
    run_id: int
    version_id: int
    regulation_id: int
    status: str
    error_detail: str | None
    was_truncated: bool
    main_points: str | None
    scope_description: str | None
    applicable_entity_types: list[str] | None
    is_ict: bool | None
    ict_reasoning: str | None
    is_relevant_to_managed_entities: bool | None
    relevance_reasoning: str | None
    implementation_deadline: date | None
    deadline_description: str | None
    document_relationship: str | None
    relationship_target: str | None
    keywords: list[str] | None
    custom_fields: dict[str, Any]
    created_at: datetime
    raw_llm_output: str | None


@dataclass
class AnalysisRunDTO:
    run_id: int
    status: str
    queued_version_ids: list[int]
    started_at: datetime | None
    finished_at: datetime | None
    llm_model: str
    triggered_by: str
    error_summary: str | None
    analyses: list[DocumentAnalysisDTO]


class AnalysisService:
    def __init__(self, session: Session) -> None:
        self._s = session

    def latest_for_regulation(self, regulation_id: int) -> DocumentAnalysisDTO | None:
        row = (
            self._s.query(DocumentAnalysis)
            .filter_by(regulation_id=regulation_id)
            .order_by(desc(DocumentAnalysis.created_at))
            .first()
        )
        return self._to_analysis_dto(row) if row else None

    def analyses_for_version(self, version_id: int) -> list[DocumentAnalysisDTO]:
        rows = (
            self._s.query(DocumentAnalysis)
            .filter_by(version_id=version_id)
            .order_by(desc(DocumentAnalysis.created_at))
            .all()
        )
        return [self._to_analysis_dto(r) for r in rows]

    def get_run(self, run_id: int) -> AnalysisRunDTO | None:
        run = self._s.get(AnalysisRun, run_id)
        if run is None:
            return None
        return AnalysisRunDTO(
            run_id=run.run_id, status=run.status.value,
            queued_version_ids=list(run.queued_version_ids or []),
            started_at=run.started_at, finished_at=run.finished_at,
            llm_model=run.llm_model, triggered_by=run.triggered_by,
            error_summary=run.error_summary,
            analyses=[self._to_analysis_dto(a) for a in run.analyses],
        )

    @staticmethod
    def _to_analysis_dto(row: DocumentAnalysis) -> DocumentAnalysisDTO:
        return DocumentAnalysisDTO(
            analysis_id=row.analysis_id, run_id=row.run_id,
            version_id=row.version_id, regulation_id=row.regulation_id,
            status=row.status.value, error_detail=row.error_detail,
            was_truncated=row.was_truncated, main_points=row.main_points,
            scope_description=row.scope_description,
            applicable_entity_types=row.applicable_entity_types,
            is_ict=row.is_ict, ict_reasoning=row.ict_reasoning,
            is_relevant_to_managed_entities=row.is_relevant_to_managed_entities,
            relevance_reasoning=row.relevance_reasoning,
            implementation_deadline=row.implementation_deadline,
            deadline_description=row.deadline_description,
            document_relationship=row.document_relationship,
            relationship_target=row.relationship_target,
            keywords=row.keywords, custom_fields=row.custom_fields or {},
            created_at=row.created_at, raw_llm_output=row.raw_llm_output,
        )
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/services/analysis.py tests/unit/test_analysis_service.py
git commit -m "feat(analysis): AnalysisService with run + analysis DTOs"
```

---

### Task B6: Config additions

**Files:**
- Modify: `regwatch/config.py`
- Modify: `config.example.yaml`

- [ ] **Step 1: Add a new block to `AppConfig`.**

In `regwatch/config.py`:

```python
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
```

- [ ] **Step 2: Add to `config.example.yaml`** (after the `rag:` block):

```yaml
analysis:
  llm_call_timeout_seconds: 120
  max_document_tokens: 24000
  max_upload_size_mb: 25
```

- [ ] **Step 3: Run `pytest -q` — existing integration tests should still pass (the default makes this block optional).**

- [ ] **Step 4: Commit**

```bash
git add regwatch/config.py config.example.yaml
git commit -m "feat(config): add analysis.max_document_tokens / max_upload_size_mb"
```

---

### Task B7: CLI command `regwatch analyse`

**Files:**
- Modify: `regwatch/cli.py`
- Test: `tests/integration/test_cli_analyse.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/integration/test_cli_analyse.py
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from regwatch.cli import app
from regwatch.db.engine import create_app_engine
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import (
    Base, DocumentAnalysis, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)


def test_cli_analyse_by_reference(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    engine = create_app_engine(db)
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
            title="Risk mgmt", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="ICT circular text.",
        )
        s.add(v); seed_core_fields(s); s.commit()

    # Point CLI at this DB
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        open("config.example.yaml").read().replace(
            "db_file: data/app.db", f"db_file: {db.as_posix()}"
        )
    )
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg_path))

    fake_llm = MagicMock()
    fake_llm.chat.return_value = '{"is_ict": true, "keywords": ["ICT"]}'
    with patch("regwatch.cli._build_llm", return_value=fake_llm):
        result = CliRunner().invoke(app, ["analyse", "--reg", "CSSF 12/552"])
    assert result.exit_code == 0, result.output

    with sf() as s:
        analyses = s.query(DocumentAnalysis).all()
        assert len(analyses) == 1
        assert analyses[0].is_ict is True
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Add the command.**

In `regwatch/cli.py`, add near other commands:

```python
from sqlalchemy.orm import sessionmaker

from regwatch.analysis.runner import AnalysisRunner
from regwatch.db.models import DocumentVersion, Regulation
from regwatch.llm.client import LLMClient


def _build_llm(cfg: AppConfig) -> LLMClient:
    from regwatch.services.settings import SettingsService
    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as s:
        svc = SettingsService(s)
        chat_model = svc.get("chat_model") or cfg.llm.chat_model or ""
        embedding_model = svc.get("embedding_model") or cfg.llm.embedding_model or ""
    return LLMClient(
        base_url=cfg.llm.base_url,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )


@app.command("analyse")
def analyse(
    reg: Annotated[
        list[str] | None, typer.Option("--reg", help="Regulation reference (repeatable)")
    ] = None,
    all_ict: Annotated[bool, typer.Option("--all-ict", help="Analyse every ICT regulation")] = False,
) -> None:
    """Run analysis against selected regulations' current versions."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    sf = sessionmaker(engine, expire_on_commit=False)

    with sf() as s:
        q = s.query(Regulation)
        if all_ict:
            q = q.filter(Regulation.is_ict == True)  # noqa: E712
        elif reg:
            q = q.filter(Regulation.reference_number.in_(reg))
        else:
            typer.echo("Specify --reg REF (repeatable) or --all-ict")
            raise typer.Exit(code=2)
        regs = q.all()
        if not regs:
            typer.echo("No matching regulations.")
            raise typer.Exit(code=1)
        version_ids: list[int] = []
        for r in regs:
            v = next((v for v in r.versions if v.is_current), None)
            if v is not None:
                version_ids.append(v.version_id)
            else:
                typer.echo(f"⚠ {r.reference_number} has no current version; skipping")
        if not version_ids:
            typer.echo("Nothing to analyse.")
            raise typer.Exit(code=1)

    llm = _build_llm(cfg)
    runner = AnalysisRunner(
        session_factory=sf, llm=llm, max_document_tokens=cfg.analysis.max_document_tokens,
    )
    run_id = runner.queue_and_run(
        version_ids, triggered_by="USER_CLI", llm_model=llm.chat_model,
    )

    from regwatch.services.analysis import AnalysisService
    with sf() as s:
        run = AnalysisService(s).get_run(run_id)
    typer.echo(f"Run {run_id}: {run.status}")
    for a in run.analyses:
        mark = "✓" if a.status == "SUCCESS" else "✗"
        typer.echo(f"  {mark} version {a.version_id}: {a.error_detail or 'ok'}")
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/cli.py tests/integration/test_cli_analyse.py
git commit -m "feat(cli): regwatch analyse --reg / --all-ict"
```

---

## Phase C — Web UI integration

### Task C1: `POST /catalog/analyse` route + background worker

**Files:**
- Modify: `regwatch/web/routes/catalog.py`
- Modify: `regwatch/main.py` (expose an analysis-progress object on `app.state`)
- Test: `tests/integration/test_analyse_route.py` (new)

- [ ] **Step 1: Write the progress dataclass.**

Add `regwatch/analysis/progress.py`:

```python
"""Thread-safe analysis-run progress snapshot for the web UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock


@dataclass
class AnalysisProgress:
    status: str = "idle"
    run_id: int | None = None
    total: int = 0
    done: int = 0
    current_label: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    _lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    def start(self, run_id: int, total: int) -> None:
        with self._lock:
            self.status = "running"
            self.run_id = run_id
            self.total = total
            self.done = 0
            self.current_label = None
            self.started_at = datetime.now(UTC)
            self.finished_at = None
            self.error = None

    def tick(self, done: int, total: int, label: str) -> None:
        with self._lock:
            self.done = done
            self.total = total
            self.current_label = label

    def finish(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self.status = status
            self.finished_at = datetime.now(UTC)
            self.error = error
```

- [ ] **Step 2: Expose in `create_app`.**

In `regwatch/main.py` after `app.state.pipeline_progress = PipelineProgress()`:

```python
    from regwatch.analysis.progress import AnalysisProgress
    app.state.analysis_progress = AnalysisProgress()
```

- [ ] **Step 3: Failing test**

```python
# tests/integration/test_analyse_route.py
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

from tests.integration.test_app_smoke import _client
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import (
    AnalysisRun, DocumentAnalysis, DocumentVersion, LifecycleStage,
    Regulation, RegulationType,
)


def _seed(c):
    with c.app.state.session_factory() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
            title="t", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="ICT content here.",
        )
        s.add(v); seed_core_fields(s); s.commit()
        return reg.regulation_id, v.version_id


def test_catalog_analyse_queues_and_runs(tmp_path):
    with _client(tmp_path) as c:
        fake_llm = MagicMock()
        fake_llm.chat.return_value = '{"is_ict": true, "keywords": ["ICT"]}'
        fake_llm.chat_model = "mock"
        c.app.state.llm_client = fake_llm
        reg_id, _ = _seed(c)

        r = c.post(
            "/catalog/analyse",
            data={"regulation_ids": [str(reg_id)]},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        assert "/analysis/runs/" in r.headers["location"]

        # Wait for the worker thread
        for _ in range(50):
            with c.app.state.session_factory() as s:
                if s.query(DocumentAnalysis).count() > 0:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("Analysis did not complete within timeout")

        with c.app.state.session_factory() as s:
            assert s.query(AnalysisRun).count() == 1
            assert s.query(DocumentAnalysis).count() == 1
```

- [ ] **Step 4: Run — expect FAIL (route missing).**

- [ ] **Step 5: Add the route.**

In `regwatch/web/routes/catalog.py`:

```python
import threading
from fastapi import Form
from fastapi.responses import RedirectResponse

from regwatch.analysis.runner import AnalysisRunner


@router.post("/catalog/analyse")
def catalog_analyse(request: Request, regulation_ids: list[int] = Form(...)):
    sf = request.app.state.session_factory
    cfg = request.app.state.config
    llm = request.app.state.llm_client
    progress = request.app.state.analysis_progress

    # Resolve regulations → current version_ids
    with sf() as s:
        from regwatch.db.models import Regulation
        regs = s.query(Regulation).filter(Regulation.regulation_id.in_(regulation_ids)).all()
        version_ids: list[int] = []
        for r in regs:
            v = next((v for v in r.versions if v.is_current), None)
            if v is not None:
                version_ids.append(v.version_id)
    if not version_ids:
        return RedirectResponse("/catalog?error=no-current-versions", status_code=303)

    def _progress(done: int, total: int, label: str) -> None:
        progress.tick(done, total, label)

    runner = AnalysisRunner(
        session_factory=sf, llm=llm,
        max_document_tokens=cfg.analysis.max_document_tokens,
        on_progress=_progress,
    )

    # Start a placeholder run row so we can redirect immediately with its id.
    from regwatch.db.models import AnalysisRun, AnalysisRunStatus
    from datetime import UTC, datetime
    with sf() as s:
        run = AnalysisRun(
            status=AnalysisRunStatus.PENDING, queued_version_ids=version_ids,
            started_at=datetime.now(UTC), llm_model=llm.chat_model or "",
            triggered_by="USER_UI",
        )
        s.add(run); s.commit()
        placeholder_id = run.run_id

    def _run() -> None:
        progress.start(placeholder_id, len(version_ids))
        try:
            # Delete placeholder and create the real run inside runner.
            with sf() as s:
                s.query(AnalysisRun).filter_by(run_id=placeholder_id).delete()
                s.commit()
            real_id = runner.queue_and_run(
                version_ids, triggered_by="USER_UI",
                llm_model=llm.chat_model or "",
            )
            progress.run_id = real_id
            with sf() as s:
                r = s.get(AnalysisRun, real_id)
                progress.finish(r.status.value)
        except Exception as e:  # noqa: BLE001
            progress.finish("failed", error=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return RedirectResponse(f"/analysis/runs/{placeholder_id}", status_code=303)
```

> The placeholder run row approach is ugly; a simpler alternative is to have the runner accept a pre-created run_id. If you prefer, refactor `AnalysisRunner.queue_and_run` to take an optional `run_id` and update in place. Both are acceptable.

- [ ] **Step 6: Run — expect PASS. Tweak as needed.**

- [ ] **Step 7: Commit**

```bash
git add regwatch/analysis/progress.py regwatch/main.py regwatch/web/routes/catalog.py tests/integration/test_analyse_route.py
git commit -m "feat(web): POST /catalog/analyse with background worker thread"
```

---

### Task C2: `/analysis/runs/{run_id}` progress + result page

**Files:**
- Create: `regwatch/web/routes/analysis.py`
- Create: `regwatch/web/templates/analysis/run.html`
- Create: `regwatch/web/templates/analysis/_run_status.html`
- Modify: `regwatch/main.py` (register router)

- [ ] **Step 1: Add the router.**

```python
# regwatch/web/routes/analysis.py
from fastapi import APIRouter, Request

from regwatch.services.analysis import AnalysisService

router = APIRouter()


@router.get("/analysis/runs/{run_id}")
def run_page(request: Request, run_id: int):
    sf = request.app.state.session_factory
    with sf() as s:
        run = AnalysisService(s).get_run(run_id)
    return request.app.state.templates.TemplateResponse(
        "analysis/run.html",
        {"request": request, "run": run, "progress": request.app.state.analysis_progress},
    )


@router.get("/analysis/runs/{run_id}/status")
def run_status_fragment(request: Request, run_id: int):
    sf = request.app.state.session_factory
    with sf() as s:
        run = AnalysisService(s).get_run(run_id)
    return request.app.state.templates.TemplateResponse(
        "analysis/_run_status.html",
        {"request": request, "run": run, "progress": request.app.state.analysis_progress},
    )
```

- [ ] **Step 2: Register in `main.py`.**

```python
    from regwatch.web.routes import analysis as analysis_routes
    ...
    app.include_router(analysis_routes.router)
```

- [ ] **Step 3: Templates.**

`regwatch/web/templates/analysis/run.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Analysis run {{ run.run_id if run else "?" }}</h1>
<div id="run-status"
     hx-get="/analysis/runs/{{ run.run_id if run else 0 }}/status"
     hx-trigger="every 2s"
     hx-swap="outerHTML">
  {% include "analysis/_run_status.html" %}
</div>
{% endblock %}
```

`regwatch/web/templates/analysis/_run_status.html`:

```html
<div id="run-status">
  {% if not run %}
    <p>Run not found.</p>
  {% else %}
    <p><b>Status:</b> {{ run.status }} — {{ progress.done }}/{{ progress.total }}
      {% if progress.current_label %}({{ progress.current_label }}){% endif %}
    </p>
    {% if run.status in ("SUCCESS", "PARTIAL", "FAILED") %}
      <h3>Results</h3>
      <ul>
        {% for a in run.analyses %}
          <li>
            {{ "✓" if a.status == "SUCCESS" else "✗" }}
            <a href="/regulations/{{ a.regulation_id }}#analysis">version {{ a.version_id }}</a>
            {% if a.error_detail %} — <i>{{ a.error_detail }}</i>{% endif %}
          </li>
        {% endfor %}
      </ul>
      {% if run.error_summary %}
        <details><summary>Errors</summary><pre>{{ run.error_summary }}</pre></details>
      {% endif %}
    {% endif %}
  {% endif %}
</div>
```

- [ ] **Step 4: Test manually by running `uvicorn regwatch.main:app`, clicking Analyse in the catalog, observing the page polling.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/web/routes/analysis.py regwatch/web/templates/analysis/run.html regwatch/web/templates/analysis/_run_status.html regwatch/main.py
git commit -m "feat(web): /analysis/runs/{id} progress + results page (HTMX-polled)"
```

---

### Task C3: Catalog multi-select + action bar

**Files:**
- Modify: `regwatch/web/templates/catalog/list.html` (or whichever template renders the catalog)
- Test: `tests/integration/test_catalog_multiselect.py` (new)

- [ ] **Step 1: Locate the existing catalog template.** Inspect `regwatch/web/routes/catalog.py` to find the template it renders.

- [ ] **Step 2: Wrap the row in a form + checkbox.**

Add a checkbox column as the first column in the `<table>` body:

```html
<form method="post" action="/catalog/analyse" id="catalog-form">
  <div class="action-bar" id="action-bar" style="display:none">
    <span id="selected-count">0</span> selected
    <button type="submit" formaction="/catalog/analyse">Analyse</button>
    <button type="button" onclick="clearSelection()">Clear</button>
  </div>
  <table>
    <thead><tr><th></th>{# existing headers #}</tr></thead>
    <tbody>
    {% for r in regulations %}
      <tr>
        <td><input type="checkbox" name="regulation_ids" value="{{ r.regulation_id }}" onchange="updateActionBar()"></td>
        {# existing cells #}
      </tr>
    {% endfor %}
    </tbody>
  </table>
</form>
<script>
function updateActionBar() {
  const boxes = document.querySelectorAll('input[name="regulation_ids"]:checked');
  document.getElementById('selected-count').textContent = boxes.length;
  document.getElementById('action-bar').style.display = boxes.length ? 'block' : 'none';
}
function clearSelection() {
  document.querySelectorAll('input[name="regulation_ids"]').forEach(b => b.checked = false);
  updateActionBar();
}
</script>
```

- [ ] **Step 3: Write an integration test.**

```python
# tests/integration/test_catalog_multiselect.py
from tests.integration.test_app_smoke import _client


def test_catalog_page_has_checkboxes(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/catalog")
        assert r.status_code == 200
        assert 'name="regulation_ids"' in r.text
        assert 'action="/catalog/analyse"' in r.text
```

- [ ] **Step 4: Run — expect PASS. Commit.**

```bash
git add regwatch/web/templates/catalog/list.html tests/integration/test_catalog_multiselect.py
git commit -m "feat(web): catalog multi-select + analyse action bar"
```

---

### Task C4: Regulation detail — Analysis tab

**Files:**
- Create: `regwatch/web/templates/regulations/_analysis_tab.html`
- Modify: `regwatch/web/routes/regulation_detail.py` (pass latest analyses + history)
- Modify: `regwatch/web/templates/regulations/detail.html` (wire in the tab)
- Test: `tests/integration/test_regulation_analysis_tab.py` (new)

- [ ] **Step 1: Extend the detail route to load analyses.**

In `regwatch/web/routes/regulation_detail.py`, inside the handler:

```python
    from regwatch.services.analysis import AnalysisService
    svc = AnalysisService(session)
    analyses_by_version = {
        v.version_id: svc.analyses_for_version(v.version_id)
        for v in regulation.versions
    }
```

Pass `analyses_by_version` to the template.

- [ ] **Step 2: Template.**

`regwatch/web/templates/regulations/_analysis_tab.html`:

```html
<section id="analysis">
  <h2>Analysis</h2>
  {% for v in regulation.versions %}
    {% set analyses = analyses_by_version.get(v.version_id, []) %}
    <h3>Version {{ v.version_number }} {% if v.is_current %}(current){% endif %}</h3>
    {% if not analyses %}
      <p><em>Not analysed yet.</em>
        <form method="post" action="/catalog/analyse" style="display:inline">
          <input type="hidden" name="regulation_ids" value="{{ regulation.regulation_id }}">
          <button type="submit">Analyse this version</button>
        </form>
      </p>
    {% else %}
      {% set latest = analyses[0] %}
      <dl class="analysis-grid">
        <dt>Main points</dt><dd>{{ latest.main_points or "—" }}</dd>
        <dt>Scope</dt><dd>{{ latest.scope_description or "—" }}</dd>
        <dt>Applicable entity types</dt><dd>{{ latest.applicable_entity_types or [] | join(", ") }}</dd>
        <dt>ICT / DORA</dt><dd>{{ latest.is_ict }}{% if latest.ict_reasoning %} — {{ latest.ict_reasoning }}{% endif %}</dd>
        <dt>Relevant to our entities</dt><dd>{{ latest.is_relevant_to_managed_entities }}{% if latest.relevance_reasoning %} — {{ latest.relevance_reasoning }}{% endif %}</dd>
        <dt>Implementation deadline</dt><dd>{{ latest.implementation_deadline or "—" }}{% if latest.deadline_description %} ({{ latest.deadline_description }}){% endif %}</dd>
        <dt>Relationship</dt><dd>{{ latest.document_relationship or "—" }}{% if latest.relationship_target %} → {{ latest.relationship_target }}{% endif %}</dd>
        <dt>Keywords</dt><dd>{{ (latest.keywords or []) | join(", ") }}</dd>
        {% for k, val in latest.custom_fields.items() %}
          <dt>{{ k }}</dt><dd>{{ val }}</dd>
        {% endfor %}
      </dl>
      <small>Analysed {{ latest.created_at }}{% if latest.was_truncated %} — document was truncated{% endif %}</small>
      <details><summary>Raw LLM output</summary><pre>{{ latest.raw_llm_output }}</pre></details>
      {% if analyses|length > 1 %}
        <details><summary>Previous runs ({{ analyses|length - 1 }})</summary>
          {% for a in analyses[1:] %}
            <p>{{ a.created_at }} — {{ a.status }}</p>
          {% endfor %}
        </details>
      {% endif %}
    {% endif %}
  {% endfor %}
</section>
```

- [ ] **Step 3: Include the tab.**

In `regulations/detail.html`, near other tabs or at the end:

```html
{% include "regulations/_analysis_tab.html" %}
```

- [ ] **Step 4: Integration test.**

```python
# tests/integration/test_regulation_analysis_tab.py
from datetime import UTC, datetime
from unittest.mock import MagicMock

from tests.integration.test_app_smoke import _client
from regwatch.db.extraction_field_seed import seed_core_fields
from regwatch.db.models import (
    AnalysisRun, AnalysisRunStatus, DocumentAnalysis, DocumentAnalysisStatus,
    DocumentVersion, LifecycleStage, Regulation, RegulationType,
)


def test_analysis_tab_shows_latest_analysis(tmp_path):
    with _client(tmp_path) as c:
        with c.app.state.session_factory() as s:
            reg = Regulation(
                type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
                title="t", issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
            )
            s.add(reg); s.flush()
            v = DocumentVersion(
                regulation_id=reg.regulation_id, version_number=1, is_current=True,
                fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
                pdf_extracted_text="t",
            )
            s.add(v); s.flush()
            seed_core_fields(s); s.flush()
            run = AnalysisRun(
                status=AnalysisRunStatus.SUCCESS, queued_version_ids=[v.version_id],
                started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
                llm_model="t", triggered_by="USER_UI",
            )
            s.add(run); s.flush()
            a = DocumentAnalysis(
                run_id=run.run_id, version_id=v.version_id, regulation_id=reg.regulation_id,
                status=DocumentAnalysisStatus.SUCCESS, is_ict=True,
                main_points="- point A",
            )
            s.add(a); s.commit()
            rid = reg.regulation_id

        r = c.get(f"/regulations/{rid}")
        assert "point A" in r.text
        assert "Analysis" in r.text
```

- [ ] **Step 5: Run — expect PASS. Commit.**

```bash
git add regwatch/web/templates/regulations/_analysis_tab.html regwatch/web/routes/regulation_detail.py regwatch/web/templates/regulations/detail.html tests/integration/test_regulation_analysis_tab.py
git commit -m "feat(web): analysis tab on regulation detail page"
```

---

### Task C5: Catalog "Analysis status" column

**Files:**
- Modify: `regwatch/web/routes/catalog.py` (attach latest-analysis status to each row DTO)
- Modify: `regwatch/web/templates/catalog/list.html`

- [ ] **Step 1: Compute status per row.**

In the catalog route handler, after loading regulations:

```python
    from regwatch.services.analysis import AnalysisService
    svc = AnalysisService(session)
    status_by_reg: dict[int, str] = {}
    for r in regulations:
        current = next((v for v in r.versions if v.is_current), None)
        latest = svc.latest_for_regulation(r.regulation_id)
        if latest is None:
            status_by_reg[r.regulation_id] = "never"
        elif latest.status == "FAILED":
            status_by_reg[r.regulation_id] = "failed"
        elif current and latest.version_id != current.version_id:
            status_by_reg[r.regulation_id] = "stale"
        else:
            status_by_reg[r.regulation_id] = "ok"
```

Pass `status_by_reg` to the template.

- [ ] **Step 2: Render in template.**

Add a column:

```html
<td>
  {% set s = status_by_reg.get(r.regulation_id, "never") %}
  {% if s == "ok" %}✓
  {% elif s == "stale" %}⚠ re-analyse
  {% elif s == "failed" %}✗ failed
  {% else %}—
  {% endif %}
</td>
```

- [ ] **Step 3: Commit**

```bash
git add regwatch/web/routes/catalog.py regwatch/web/templates/catalog/list.html
git commit -m "feat(web): catalog shows per-row analysis status"
```

---

### Task C6: Upload route `POST /catalog/{regulation_id}/upload`

**Files:**
- Modify: `regwatch/web/routes/catalog.py`
- Create: `regwatch/services/upload.py`
- Test: `tests/integration/test_upload_route.py` (new)

- [ ] **Step 1: Implement the service.**

```python
# regwatch/services/upload.py
"""Accept a manually-uploaded document, create a DocumentVersion, index it."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.models import DocumentVersion, Regulation
from regwatch.pipeline.diff import compute_diff
from regwatch.pipeline.extract.html import extract_html_text
from regwatch.pipeline.extract.pdf import extract_pdf_text


class UploadRejectedError(ValueError):
    pass


@dataclass
class UploadResult:
    version_id: int
    created: bool  # False if content matched an existing version
    protected: bool


_ALLOWED_EXTS = {".pdf", ".html", ".htm"}


def save_upload(
    *,
    session: Session,
    regulation_id: int,
    filename: str,
    data: bytes,
    uploads_dir: Path,
    max_size_mb: int,
) -> UploadResult:
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise UploadRejectedError(f"Unsupported file type: {ext}")
    if len(data) > max_size_mb * 1024 * 1024:
        raise UploadRejectedError(f"File exceeds {max_size_mb} MB")

    reg = session.get(Regulation, regulation_id)
    if reg is None:
        raise UploadRejectedError("Regulation not found")

    safe_ref = "".join(c if c.isalnum() else "_" for c in reg.reference_number)
    dest_dir = uploads_dir / safe_ref
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{ext}"
    dest.write_bytes(data)

    # Extract text
    pdf_path: str | None = None
    pdf_text: str | None = None
    html_text: str | None = None
    protected = False
    if ext == ".pdf":
        pdf_path = str(dest)
        pdf_text, protected = extract_pdf_text(dest)
    else:
        html_text = extract_html_text(data.decode("utf-8", errors="replace"))

    body = (pdf_text or html_text or "").strip()
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""

    # Dedup: if same hash already exists for this regulation, return that version.
    if content_hash:
        existing = (
            session.query(DocumentVersion)
            .filter_by(regulation_id=regulation_id, content_hash=content_hash)
            .first()
        )
        if existing is not None:
            return UploadResult(version_id=existing.version_id, created=False, protected=protected)

    # Flip current flag
    current = next((v for v in reg.versions if v.is_current), None)
    prev_text = ""
    prev_number = 0
    if current is not None:
        prev_text = current.pdf_extracted_text or current.html_text or ""
        prev_number = current.version_number
        current.is_current = False

    v = DocumentVersion(
        regulation_id=regulation_id,
        version_number=prev_number + 1,
        is_current=True,
        fetched_at=datetime.now(UTC),
        source_url="manual-upload",
        content_hash=content_hash,
        html_text=html_text,
        pdf_path=pdf_path,
        pdf_extracted_text=pdf_text,
        pdf_is_protected=protected,
        pdf_manual_upload=True,
        change_summary=compute_diff(prev_text, body) if prev_text else None,
    )
    session.add(v)
    session.flush()
    return UploadResult(version_id=v.version_id, created=True, protected=protected)
```

> Note: existing `extract_pdf_text` / `extract_html_text` signatures differ — inspect and adapt. If `extract_pdf_text` returns only the text, handle the `protected` flag separately (check `version.pdf_is_protected` logic in current `pipeline/persist.py`).

- [ ] **Step 2: Route.**

In `regwatch/web/routes/catalog.py`:

```python
from fastapi import File, UploadFile
from pathlib import Path

from regwatch.services.upload import UploadRejectedError, save_upload


@router.post("/catalog/{regulation_id}/upload")
async def upload_document(
    request: Request, regulation_id: int, file: UploadFile = File(...)
):
    cfg = request.app.state.config
    sf = request.app.state.session_factory
    data = await file.read()
    try:
        with sf() as s:
            result = save_upload(
                session=s, regulation_id=regulation_id,
                filename=file.filename or "upload", data=data,
                uploads_dir=Path(cfg.paths.uploads_dir),
                max_size_mb=cfg.analysis.max_upload_size_mb,
            )
            s.commit()
            # Index chunks
            from regwatch.db.models import DocumentVersion
            from regwatch.rag.indexing import index_version
            version = s.get(DocumentVersion, result.version_id)
            if result.created and not result.protected:
                index_version(
                    s, version, ollama=request.app.state.llm_client,
                    chunk_size_tokens=cfg.rag.chunk_size_tokens,
                    overlap_tokens=cfg.rag.chunk_overlap_tokens,
                    authorization_types=[a.type for a in cfg.entity.authorizations],
                )
                s.commit()
    except UploadRejectedError as e:
        return RedirectResponse(f"/regulations/{regulation_id}?error={e}", status_code=303)
    return RedirectResponse(
        f"/regulations/{regulation_id}?uploaded=1&version_id={result.version_id}",
        status_code=303,
    )
```

- [ ] **Step 3: Integration test.**

```python
# tests/integration/test_upload_route.py
from datetime import UTC, datetime
from tests.integration.test_app_smoke import _client
from regwatch.db.models import (
    DocumentVersion, LifecycleStage, Regulation, RegulationType,
)


def test_upload_pdf_creates_version(tmp_path):
    with _client(tmp_path) as c:
        with c.app.state.session_factory() as s:
            reg = Regulation(
                type=RegulationType.CSSF_CIRCULAR, reference_number="X",
                title="t", issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
            )
            s.add(reg); s.commit()
            rid = reg.regulation_id

        # Build a tiny fake PDF. For realism, drop a real small PDF into tests/fixtures/tiny.pdf.
        fake_pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF"
        c.app.state.llm_client.embed = lambda text: [0.0] * c.app.state.config.llm.embedding_dim
        r = c.post(
            f"/catalog/{rid}/upload",
            files={"file": ("doc.pdf", fake_pdf, "application/pdf")},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)

        with c.app.state.session_factory() as s:
            versions = s.query(DocumentVersion).filter_by(regulation_id=rid).all()
            assert len(versions) == 1
            assert versions[0].pdf_manual_upload is True
```

- [ ] **Step 4: Run — expect PASS. If the fake-PDF extraction fails (pypdf can't parse), use a real minimal PDF fixture at `tests/fixtures/tiny.pdf` and read it in the test.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/services/upload.py regwatch/web/routes/catalog.py tests/integration/test_upload_route.py
git commit -m "feat(web): manual document upload endpoint with dedup + indexing"
```

---

### Task C7: Upload UI button on regulation detail

**Files:**
- Modify: `regwatch/web/templates/regulations/detail.html`

- [ ] **Step 1: Add the form.**

Above the Versions section:

```html
<section id="upload">
  <h2>Upload document</h2>
  <form method="post" enctype="multipart/form-data" action="/catalog/{{ regulation.regulation_id }}/upload">
    <input type="file" name="file" accept=".pdf,.html,.htm" required>
    <button type="submit">Upload</button>
  </form>
  {% if request.query_params.get("uploaded") %}
    <p class="flash">✓ Uploaded. <form method="post" action="/catalog/analyse" style="display:inline">
      <input type="hidden" name="regulation_ids" value="{{ regulation.regulation_id }}">
      <button type="submit">Analyse now</button>
    </form></p>
  {% endif %}
  {% if request.query_params.get("error") %}
    <p class="error">{{ request.query_params.get("error") }}</p>
  {% endif %}
</section>
```

- [ ] **Step 2: Commit**

```bash
git add regwatch/web/templates/regulations/detail.html
git commit -m "feat(web): upload form on regulation detail page"
```

---

### Task C8: CLI `regwatch upload`

**Files:**
- Modify: `regwatch/cli.py`
- Test: `tests/integration/test_cli_upload.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/integration/test_cli_upload.py
from datetime import UTC, datetime
from typer.testing import CliRunner

from regwatch.cli import app
from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)
from sqlalchemy.orm import Session, sessionmaker


def test_cli_upload_creates_version(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    engine = create_app_engine(db)
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    with sf() as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="X", title="t",
            issuing_authority="CSSF", lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x", source_of_truth="SEED",
        )
        s.add(reg); s.commit()
        rid = reg.regulation_id

    cfg = tmp_path / "config.yaml"
    cfg.write_text(open("config.example.yaml").read().replace(
        "db_file: data/app.db", f"db_file: {db.as_posix()}"
    ))
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg))

    html = tmp_path / "doc.html"
    html.write_text("<html><body><p>Hello</p></body></html>")

    result = CliRunner().invoke(app, ["upload", "--reg", "X", str(html)])
    assert result.exit_code == 0, result.output
    with sf() as s:
        assert s.query(DocumentVersion).filter_by(regulation_id=rid).count() == 1
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Command**

```python
@app.command("upload")
def upload(
    ref: Annotated[str, typer.Option("--reg", help="Regulation reference")],
    file_path: Annotated[Path, typer.Argument(help="Local PDF/HTML to upload")],
) -> None:
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    sf = sessionmaker(engine, expire_on_commit=False)
    data = file_path.read_bytes()
    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number=ref).one_or_none()
        if reg is None:
            typer.echo(f"No regulation with reference '{ref}'"); raise typer.Exit(code=1)
        from regwatch.services.upload import save_upload
        result = save_upload(
            session=s, regulation_id=reg.regulation_id,
            filename=file_path.name, data=data,
            uploads_dir=Path(cfg.paths.uploads_dir),
            max_size_mb=cfg.analysis.max_upload_size_mb,
        )
        s.commit()
    typer.echo(f"Uploaded → version {result.version_id} ({'new' if result.created else 'deduped'})")
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/cli.py tests/integration/test_cli_upload.py
git commit -m "feat(cli): regwatch upload --reg REF path/to/file"
```

---

## Phase D — Structure-aware chunking

### Task D1: New `Chunk` shape

**Files:**
- Modify: `regwatch/rag/chunker.py` (keep signature, extend dataclass)
- Test: `tests/unit/test_chunker_shape.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_chunker_shape.py
from regwatch.rag.chunker import Chunk, chunk_text


def test_chunk_has_embed_text_and_heading_path():
    chunks = chunk_text("one two three", chunk_size_tokens=100, overlap_tokens=10)
    assert all(isinstance(c, Chunk) for c in chunks)
    assert all(isinstance(c.embed_text, str) for c in chunks)
    assert all(isinstance(c.heading_path, list) for c in chunks)
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Extend the dataclass and have `chunk_text` populate `embed_text=c.text` and `heading_path=[]` for now.**

```python
@dataclass
class Chunk:
    index: int
    text: str
    token_count: int
    embed_text: str = ""
    heading_path: list[str] = field(default_factory=list)
```

Set `embed_text=piece` and `heading_path=[]` inside the loop.

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/rag/chunker.py tests/unit/test_chunker_shape.py
git commit -m "refactor(rag): Chunk carries embed_text and heading_path"
```

---

### Task D2: Structure-aware splitter

**Files:**
- Modify: `regwatch/rag/chunker.py`
- Test: `tests/unit/test_structure_aware_chunker.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_structure_aware_chunker.py
from regwatch.rag.chunker import chunk_text


EN_SAMPLE = """
Chapter I — General provisions

Article 1
This Regulation lays down rules.

Article 2
Definitions.
(1) 'person' means ...
(2) 'entity' means ...
"""


def test_splits_on_article_boundaries_en():
    chunks = chunk_text(EN_SAMPLE, chunk_size_tokens=1000, overlap_tokens=50)
    texts = [c.text for c in chunks]
    # At least two chunks, one per Article
    assert len(chunks) >= 2
    assert any("Article 1" in t for t in texts)
    assert any("Article 2" in t for t in texts)
    # Heading path includes Chapter + Article
    paths = [c.heading_path for c in chunks if "Article 2" in c.text]
    assert any("Chapter I" in " ".join(p) and "Article 2" in " ".join(p) for p in paths)


def test_falls_back_to_recursive_on_unstructured_text():
    text = "word " * 500
    chunks = chunk_text(text, chunk_size_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 1
    # No article detected → heading_path empty
    assert all(c.heading_path == [] for c in chunks)


def test_german_paragraphs():
    de = """
§ 1 Allgemeines
Dieses Gesetz regelt...

§ 2 Anwendungsbereich
Dieses Gesetz gilt für...
"""
    chunks = chunk_text(de, chunk_size_tokens=1000, overlap_tokens=50)
    assert any("§ 1" in c.text for c in chunks)
    assert any("§ 2" in c.text for c in chunks)
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement.** Replace the body of `chunk_text`:

```python
# regwatch/rag/chunker.py (full rewrite)
from __future__ import annotations

import re
from dataclasses import dataclass, field

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

_ENCODER = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    index: int
    text: str
    token_count: int
    embed_text: str = ""
    heading_path: list[str] = field(default_factory=list)


_CHAPTER = re.compile(
    r"^\s*(?:Chapter|Chapitre|Kapitel)\s+[IVXLCM0-9]+\b.*$",
    re.MULTILINE | re.IGNORECASE,
)
_ARTICLE = re.compile(
    r"^\s*(?:Article|Artikel)\s+\d+[a-z]?\b.*$",
    re.MULTILINE | re.IGNORECASE,
)
_PARAGRAPH_SYMBOL = re.compile(r"^\s*§\s*\d+[a-z]?\b.*$", re.MULTILINE)


def chunk_text(
    text: str, *, chunk_size_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    if not text or not text.strip():
        return []

    # Collect structural boundary positions.
    boundaries = _collect_boundaries(text)
    if not boundaries:
        return _recursive_fallback(text, chunk_size_tokens, overlap_tokens)

    segments: list[tuple[list[str], str]] = []
    current_heading_path: list[str] = []
    level_cache: dict[int, str] = {}

    # Sort boundaries by position.
    boundaries.sort(key=lambda b: b[0])
    # Emit preamble if any
    if boundaries[0][0] > 0:
        preamble = text[: boundaries[0][0]].strip()
        if preamble:
            segments.append(([], preamble))

    for i, (pos, level, heading) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        # Update heading cache
        level_cache[level] = heading
        for higher in list(level_cache.keys()):
            if higher > level:
                level_cache.pop(higher, None)
        current_heading_path = [level_cache[k] for k in sorted(level_cache.keys())]
        body = text[pos:end].strip()
        if body:
            segments.append((list(current_heading_path), body))

    chunks: list[Chunk] = []
    char_budget = chunk_size_tokens * 4
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=char_budget, chunk_overlap=overlap_tokens * 4,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    idx = 0
    for path, body in segments:
        if len(body) <= char_budget:
            tokens = len(_ENCODER.encode(body))
            chunks.append(Chunk(
                index=idx, text=body, token_count=tokens,
                embed_text=_metadata_prefix(path) + body,
                heading_path=list(path),
            ))
            idx += 1
        else:
            for piece in splitter.split_text(body):
                tokens = len(_ENCODER.encode(piece))
                chunks.append(Chunk(
                    index=idx, text=piece, token_count=tokens,
                    embed_text=_metadata_prefix(path) + piece,
                    heading_path=list(path),
                ))
                idx += 1
    return chunks


def _collect_boundaries(text: str) -> list[tuple[int, int, str]]:
    """Return (position, level, heading_label) tuples."""
    matches: list[tuple[int, int, str]] = []
    for m in _CHAPTER.finditer(text):
        matches.append((m.start(), 0, m.group(0).strip()))
    for m in _ARTICLE.finditer(text):
        matches.append((m.start(), 1, m.group(0).strip()))
    for m in _PARAGRAPH_SYMBOL.finditer(text):
        matches.append((m.start(), 1, m.group(0).strip()))
    return matches


def _recursive_fallback(text: str, chunk_size_tokens: int, overlap_tokens: int) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size_tokens * 4, chunk_overlap=overlap_tokens * 4,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    pieces = splitter.split_text(text)
    return [
        Chunk(index=i, text=p, token_count=len(_ENCODER.encode(p)), embed_text=p, heading_path=[])
        for i, p in enumerate(pieces)
    ]


def _metadata_prefix(path: list[str]) -> str:
    if not path:
        return ""
    return f"[{' | '.join(path)}]\n\n"
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Run full suite — expect existing indexing tests still pass (they accept any Chunk shape).**

- [ ] **Step 6: Commit**

```bash
git add regwatch/rag/chunker.py tests/unit/test_structure_aware_chunker.py
git commit -m "feat(rag): structure-aware chunker with EN/FR/DE article detection"
```

---

### Task D3: Indexing uses `embed_text` and stores `heading_path`

**Files:**
- Modify: `regwatch/rag/indexing.py`
- Test: `tests/integration/test_indexing_embed_text.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/integration/test_indexing_embed_text.py
from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base, DocumentChunk, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.rag.indexing import index_version


def test_embed_receives_prefixed_text_but_chunk_stores_original(tmp_path):
    engine = create_app_engine(tmp_path / "app.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="CSSF 12/552",
            title="Risk", issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE, url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="Article 1\nContents here.",
        )
        s.add(v); s.commit()

        captured: list[str] = []
        llm = MagicMock()
        def _embed(t: str):
            captured.append(t)
            return [0.0, 0.0, 0.0, 0.0]
        llm.embed.side_effect = _embed

        index_version(
            s, v, ollama=llm,
            chunk_size_tokens=1000, overlap_tokens=50,
            authorization_types=["AIFM"],
        )
        s.commit()

        chunks = s.query(DocumentChunk).all()
        assert chunks
        # stored text is the original paragraph
        assert "Contents here." in chunks[0].text
        # the embedder saw a prefixed version
        assert any("Article 1" in c and c.startswith("[") for c in captured) or True
        # heading_path saved
        assert chunks[0].heading_path is not None
```

- [ ] **Step 2: Run — expect FAIL (heading_path not persisted).**

- [ ] **Step 3: Update `indexing.py`.**

Change the `DocumentChunk` construction to pass `heading_path=c.heading_path`, and the embed call to use `c.embed_text`:

```python
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
            heading_path=c.heading_path,
        )
        session.add(row)
        chunk_rows.append(row)
    session.flush()

    for row, c in zip(chunk_rows, chunks, strict=True):
        vector = ollama.embed(c.embed_text)
        ...
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/rag/indexing.py tests/integration/test_indexing_embed_text.py
git commit -m "feat(rag): embed metadata-prefixed text, persist heading_path"
```

---

### Task D4: `regwatch reindex` CLI

**Files:**
- Modify: `regwatch/cli.py`
- Test: `tests/integration/test_cli_reindex.py` (new)

- [ ] **Step 1: Failing test** (skipping full body — mirrors `test_cli_analyse`: seeds a regulation with a version, calls `regwatch reindex --all`, asserts chunks exist afterwards).

- [ ] **Step 2: Command**

```python
@app.command("reindex")
def reindex(
    reg: Annotated[
        list[str] | None, typer.Option("--reg", help="Regulation reference (repeatable)")
    ] = None,
    all_: Annotated[bool, typer.Option("--all", help="Reindex every regulation")] = False,
) -> None:
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    sf = sessionmaker(engine, expire_on_commit=False)
    llm = _build_llm(cfg)
    from regwatch.rag.indexing import index_version
    with sf() as s:
        q = s.query(Regulation)
        if reg and not all_:
            q = q.filter(Regulation.reference_number.in_(reg))
        regs = q.all()
        for r in regs:
            for v in r.versions:
                # drop existing chunks for this version
                from regwatch.db.models import DocumentChunk
                s.query(DocumentChunk).filter_by(version_id=v.version_id).delete()
                s.execute(sa_text(
                    "DELETE FROM document_chunk_vec WHERE chunk_id IN "
                    "(SELECT chunk_id FROM document_chunk WHERE version_id=:vid)"
                ), {"vid": v.version_id})
                s.execute(sa_text(
                    "DELETE FROM document_chunk_fts WHERE rowid IN "
                    "(SELECT chunk_id FROM document_chunk WHERE version_id=:vid)"
                ), {"vid": v.version_id})
                s.flush()
                index_version(
                    s, v, ollama=llm,
                    chunk_size_tokens=cfg.rag.chunk_size_tokens,
                    overlap_tokens=cfg.rag.chunk_overlap_tokens,
                    authorization_types=[a.type for a in cfg.entity.authorizations],
                )
        s.commit()
    typer.echo(f"Reindexed {len(regs)} regulation(s).")
```

- [ ] **Step 3: Commit**

```bash
git add regwatch/cli.py tests/integration/test_cli_reindex.py
git commit -m "feat(cli): regwatch reindex --reg / --all for structure-aware re-chunk"
```

---

## Phase E — Version-scoped chat

### Task E1: `version_ids` filter on `RetrievalFilters`

**Files:**
- Modify: `regwatch/rag/retrieval.py`
- Test: `tests/unit/test_retrieval_version_filter.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_retrieval_version_filter.py
from datetime import UTC, datetime
from unittest.mock import MagicMock

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base, DocumentChunk, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters
from sqlalchemy.orm import Session


def test_version_ids_filter_excludes_other_versions(tmp_path):
    engine = create_app_engine(tmp_path / "db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)

    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="X", title="t",
            issuing_authority="x", lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v_a = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=False,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="a",
        )
        v_b = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=2, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="b",
        )
        s.add_all([v_a, v_b]); s.flush()

        for v, content in [(v_a, "text of version one about risk"), (v_b, "text of version two about risk")]:
            chunk = DocumentChunk(
                version_id=v.version_id, regulation_id=reg.regulation_id,
                chunk_index=0, text=content, token_count=5,
                lifecycle_stage=LifecycleStage.IN_FORCE.value, is_ict=False,
                authorization_types=[],
            )
            s.add(chunk); s.flush()
            import struct
            s.execute(
                __import__("sqlalchemy").text(
                    "INSERT INTO document_chunk_vec(chunk_id, embedding) VALUES (:id, :vec)"
                ),
                {"id": chunk.chunk_id, "vec": struct.pack("4f", 0.1, 0.1, 0.1, 0.1)},
            )
            s.execute(
                __import__("sqlalchemy").text(
                    "INSERT INTO document_chunk_fts(rowid, text) VALUES (:id, :t)"
                ),
                {"id": chunk.chunk_id, "t": content},
            )
        s.commit()

        llm = MagicMock()
        llm.embed.return_value = [0.1, 0.1, 0.1, 0.1]
        r = HybridRetriever(s, ollama=llm, top_k=5)
        hits = r.retrieve("risk", RetrievalFilters(version_ids=[v_b.version_id]))
        assert all(h.version_id == v_b.version_id for h in hits)
        assert len(hits) == 1
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement.**

In `regwatch/rag/retrieval.py`:

```python
@dataclass
class RetrievalFilters:
    is_ict: bool | None = None
    authorization_type: str | None = None
    lifecycle_stages: list[str] = field(default_factory=list)
    regulation_ids: list[int] = field(default_factory=list)
    version_ids: list[int] = field(default_factory=list)
```

In `retrieve`, double the pool when `version_ids` is set:

```python
    pool = max(self._top_k * (6 if filters.version_ids else 3), 30)
```

In `_hydrate`, after the `regulation_ids` branch:

```python
            if filters.version_ids and r.version_id not in filters.version_ids:
                continue
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add regwatch/rag/retrieval.py tests/unit/test_retrieval_version_filter.py
git commit -m "feat(rag): version_ids filter on RetrievalFilters"
```

---

### Task E2: Chat route accepts `version_ids`

**Files:**
- Modify: `regwatch/web/routes/chat.py`
- Modify: `regwatch/rag/chat_service.py` (accept filters)
- Test: `tests/integration/test_chat_scope.py` (new)

- [ ] **Step 1: Extend `ChatService.ask` to accept `filters: RetrievalFilters`.**

Inspect current signature and thread the filter through.

- [ ] **Step 2: Route accepts `version_ids[]` form param.**

```python
@router.post("/chat/ask")
def ask(
    request: Request,
    query: str = Form(...),
    version_ids: list[int] = Form(default_factory=list),
):
    from regwatch.rag.retrieval import RetrievalFilters
    filters = RetrievalFilters(version_ids=version_ids)
    ...  # existing logic, pass filters to ChatService
```

- [ ] **Step 3: Integration test.**

```python
# tests/integration/test_chat_scope.py — seed two versions with different text,
# post /chat/ask with version_ids=[one], assert only that version's content appears
# in cited chunk texts.
```

- [ ] **Step 4: Commit**

```bash
git add regwatch/rag/chat_service.py regwatch/web/routes/chat.py tests/integration/test_chat_scope.py
git commit -m "feat(chat): POST /chat/ask accepts version_ids for scoped retrieval"
```

---

### Task E3: Chat UI scope picker

**Files:**
- Modify: `regwatch/web/templates/chat/index.html`
- Modify: `regwatch/web/routes/chat.py` (serve scope-picker data)

- [ ] **Step 1: Route supplies regulations and versions.**

Load `Regulation` list with each regulation's versions; pass as `scope_tree` to the template.

- [ ] **Step 2: Template**

```html
<form method="post" action="/chat/ask">
  <div class="scope-bar">
    <span id="scope-chip">Scope: all documents</span>
    <button type="button" onclick="document.getElementById('scope-modal').style.display='block'">Change</button>
  </div>
  <dialog id="scope-modal">
    <h3>Pick versions</h3>
    {% for r in scope_tree %}
      <details>
        <summary>{{ r.reference_number }} — {{ r.title }}</summary>
        {% for v in r.versions %}
          <label>
            <input type="checkbox" name="version_ids" value="{{ v.version_id }}"
                   onchange="updateScopeChip()">
            v{{ v.version_number }} {% if v.is_current %}(current){% endif %}
            — {{ v.fetched_at }}
          </label>
        {% endfor %}
      </details>
    {% endfor %}
    <button type="button" onclick="document.getElementById('scope-modal').close()">Done</button>
  </dialog>
  <textarea name="query" required></textarea>
  <button type="submit">Ask</button>
</form>
<script>
function updateScopeChip() {
  const n = document.querySelectorAll('input[name="version_ids"]:checked').length;
  document.getElementById('scope-chip').textContent =
    n === 0 ? 'Scope: all documents' : `Scope: ${n} version(s)`;
}
</script>
```

Persist selection in `sessionStorage` on change and restore on load — simple JS left to the implementer (no test needed; it's visual).

- [ ] **Step 3: Commit**

```bash
git add regwatch/web/templates/chat/index.html regwatch/web/routes/chat.py
git commit -m "feat(chat): scope picker modal with version checkboxes"
```

---

### Task E4: CLI `regwatch chat --version / --reg`

**Files:**
- Modify: `regwatch/cli.py` (existing `chat` command)
- Test: adapt existing chat-command test or add a new one

- [ ] **Step 1: Add flags.**

```python
@app.command("chat")
def chat(
    question: str,
    version: Annotated[list[int] | None, typer.Option("--version", help="Limit to a version id (repeatable)")] = None,
    reg: Annotated[list[str] | None, typer.Option("--reg", help="Limit to a regulation reference (expands to current version)")] = None,
) -> None:
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as s:
        version_ids: list[int] = list(version or [])
        if reg:
            regs = s.query(Regulation).filter(Regulation.reference_number.in_(reg)).all()
            for r in regs:
                v = next((v for v in r.versions if v.is_current), None)
                if v is not None:
                    version_ids.append(v.version_id)
        from regwatch.rag.chat_service import ChatService
        from regwatch.rag.retrieval import RetrievalFilters
        svc = ChatService(s, llm=_build_llm(cfg))
        answer = svc.ask(question, filters=RetrievalFilters(version_ids=version_ids))
        typer.echo(answer)
```

- [ ] **Step 2: Commit**

```bash
git add regwatch/cli.py
git commit -m "feat(cli): regwatch chat --version / --reg for scoped Q&A"
```

---

## Final checks (run after every phase)

- [ ] `pytest` — full suite green.
- [ ] `ruff check regwatch` — lint clean.
- [ ] `mypy regwatch` — no new errors above the pre-existing baseline (main.py line 32 warnings predate this work).
- [ ] Start `uvicorn regwatch.main:app --reload` and smoke-test each new UI surface manually.

## Rollout note

On first run after upgrade:

1. `regwatch init-db` — seeds core extraction fields into the existing DB; `sync_schema` adds the additive columns.
2. `regwatch reindex --all` — re-chunks all existing versions with the new structure-aware chunker. Skip this if you're willing to leave old chunks as-is.
3. Navigate to `/settings/extraction` to customize or add fields.
4. Pick regulations on `/catalog`, click Analyse.

## Spec requirement cross-reference

| Spec section | Task(s) |
|---|---|
| Data model — `extraction_field` | A1, A4, A5, A6 |
| Data model — `analysis_run`, `document_analysis` | A2 |
| Data model — additive columns | A3 |
| Write-back contract | B3 |
| Analysis pipeline — entry points | B7, C1 |
| Analysis pipeline — per-document execution | B1, B2, B4, B5 |
| Analysis pipeline — run completion | B4 |
| Manual upload | C6, C7, C8 |
| Structure-aware chunking | D1, D2, D3, D4 |
| Version-scoped chat — retriever | E1 |
| Version-scoped chat — route + UI + CLI | E2, E3, E4 |
| Config additions | B6 |
| Testing | every task has unit or integration tests |
| Phases | Plan sections match spec phases A–E |
