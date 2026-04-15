"""SQLAlchemy ORM models for the Regulatory Watcher database."""
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class TZDateTime(TypeDecorator):
    """Store datetimes as UTC ISO strings; always read back as UTC-aware datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class AuthorizationType(StrEnum):
    AIFM = "AIFM"
    CHAPTER15_MANCO = "CHAPTER15_MANCO"


class RegulationType(StrEnum):
    LU_LAW = "LU_LAW"
    LU_GRAND_DUCAL_REGULATION = "LU_GRAND_DUCAL_REGULATION"
    LU_MINISTERIAL_REGULATION = "LU_MINISTERIAL_REGULATION"
    CSSF_CIRCULAR = "CSSF_CIRCULAR"
    CSSF_CIRCULAR_ANNEX = "CSSF_CIRCULAR_ANNEX"
    CSSF_REGULATION = "CSSF_REGULATION"
    PROFESSIONAL_STANDARD = "PROFESSIONAL_STANDARD"
    EU_REGULATION = "EU_REGULATION"
    EU_DIRECTIVE = "EU_DIRECTIVE"
    ESMA_GUIDELINE = "ESMA_GUIDELINE"
    RTS = "RTS"
    ITS = "ITS"
    DELEGATED_ACT = "DELEGATED_ACT"


class LifecycleStage(StrEnum):
    CONSULTATION = "CONSULTATION"
    PROPOSAL = "PROPOSAL"
    DRAFT_BILL = "DRAFT_BILL"
    ADOPTED_NOT_IN_FORCE = "ADOPTED_NOT_IN_FORCE"
    IN_FORCE = "IN_FORCE"
    AMENDED = "AMENDED"
    REPEALED = "REPEALED"


class DoraPillar(StrEnum):
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
    gleif_last_updated: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

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
    applicable_entity_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    transposition_done: Mapped[bool] = mapped_column(Boolean, default=False)
    application_done: Mapped[bool] = mapped_column(Boolean, default=False)

    aliases: Mapped[list[RegulationAlias]] = relationship(
        back_populates="regulation", cascade="all, delete-orphan"
    )
    applicabilities: Mapped[list[RegulationApplicability]] = relationship(
        back_populates="regulation", cascade="all, delete-orphan"
    )
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="regulation", cascade="all, delete-orphan", passive_deletes=True
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
    # PROPOSAL_OF / TRANSPOSES / AMENDS / REPEALS / SUCCEEDS
    relation: Mapped[str] = mapped_column(String(20))


class RegulationOverride(Base):
    __tablename__ = "regulation_override"

    override_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int | None] = mapped_column(
        ForeignKey("regulation.regulation_id"), nullable=True
    )
    reference_number: Mapped[str] = mapped_column(String(100))
    # Known values:
    #   "EXCLUDE"      — suppress this ref entirely during discovery;
    #                    no Regulation row is created/updated.
    #   "SET_ICT"      — force is_ict=True on the regulation (overrides heuristic).
    #   "UNSET_ICT"    — force is_ict=False (overrides heuristic).
    #   "KEEP_ACTIVE"  — exempt from auto-retirement in Task 10's
    #                    filter-matrix retirement sweep, even when absent
    #                    from every matrix cell.
    action: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)


class DocumentVersion(Base):
    __tablename__ = "document_version"

    version_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="CASCADE")
    )
    version_number: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime)
    source_url: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    html_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pdf_extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    pdf_manual_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    regulation: Mapped[Regulation] = relationship(back_populates="versions")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="version", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint(
            "regulation_id", "version_number", name="uq_document_version_regulation_version"
        ),
        Index(
            "uq_document_version_one_current",
            "regulation_id",
            unique=True,
            sqlite_where=sa_text("is_current = 1"),
        ),
    )


class UpdateEvent(Base):
    __tablename__ = "update_event"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(TZDateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_ict: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    severity: Mapped[str] = mapped_column(String(20))  # INFORMATIONAL / MATERIAL / CRITICAL
    review_status: Mapped[str] = mapped_column(
        String(20), default="NEW", index=True
    )  # NEW / SEEN / ASSESSED / ARCHIVED
    seen_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    applicable_entity_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    regulation_links: Mapped[list[UpdateEventRegulationLink]] = relationship(
        back_populates="event", cascade="all, delete-orphan", passive_deletes=True
    )


class UpdateEventRegulationLink(Base):
    __tablename__ = "update_event_regulation_link"

    link_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("update_event.event_id"))
    regulation_id: Mapped[int] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="CASCADE")
    )
    match_method: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)
    matched_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped[UpdateEvent] = relationship(back_populates="regulation_links")


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # RUNNING / COMPLETED / FAILED / ABORTED
    sources_attempted: Mapped[list[str]] = mapped_column(JSON, default=list)
    sources_failed: Mapped[list[str]] = mapped_column(JSON, default=list)
    events_created: Mapped[int] = mapped_column(Integer, default=0)
    versions_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)


class DocumentChunk(Base):
    __tablename__ = "document_chunk"

    chunk_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("document_version.version_id", ondelete="CASCADE"), index=True
    )
    regulation_id: Mapped[int] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    lifecycle_stage: Mapped[str] = mapped_column(String(30))
    is_ict: Mapped[bool] = mapped_column(Boolean, default=False)
    authorization_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    heading_path: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    version: Mapped[DocumentVersion] = relationship(back_populates="chunks")


class ChatSession(Base):
    __tablename__ = "chat_session"

    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
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
    created_at: Mapped[datetime] = mapped_column(TZDateTime)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


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
    regulation_id: Mapped[int | None] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="CASCADE"), index=True, nullable=True
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
    coercion_errors: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    llm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(UTC))

    run: Mapped[AnalysisRun] = relationship(back_populates="analyses")

    __table_args__ = (
        UniqueConstraint("version_id", "run_id", name="uq_document_analysis_version_run"),
        Index("ix_document_analysis_regulation_created", "regulation_id", "created_at"),
    )


class DiscoveryRun(Base):
    __tablename__ = "discovery_run"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(20))  # RUNNING | SUCCESS | PARTIAL | FAILED
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(20))  # USER_UI | USER_CLI | SCHEDULER
    entity_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(20))  # full | incremental

    total_scraped: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    amended_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    withdrawn_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    retired_count: Mapped[int] = mapped_column(Integer, default=0)

    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[DiscoveryRunItem]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class DiscoveryRunItem(Base):
    __tablename__ = "discovery_run_item"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("discovery_run.run_id", ondelete="CASCADE"), index=True
    )
    regulation_id: Mapped[int | None] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="SET NULL"), nullable=True
    )
    reference_number: Mapped[str] = mapped_column(String(100), index=True)
    outcome: Mapped[str] = mapped_column(
        String(30)
    )  # NEW | AMENDED | UPDATED_METADATA | UNCHANGED | WITHDRAWN | FAILED
    detail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    content_type: Mapped[str] = mapped_column(String(60), default="")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(UTC))

    run: Mapped[DiscoveryRun] = relationship(back_populates="items")


class RegulationDiscoverySource(Base):
    __tablename__ = "regulation_discovery_source"

    source_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regulation_id: Mapped[int] = mapped_column(
        ForeignKey("regulation.regulation_id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40))
    content_type: Mapped[str] = mapped_column(String(60))
    first_seen_run_id: Mapped[int] = mapped_column(
        ForeignKey("discovery_run.run_id", ondelete="CASCADE")
    )
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_seen_run_id: Mapped[int] = mapped_column(
        ForeignKey("discovery_run.run_id", ondelete="CASCADE"), index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime)

    __table_args__ = (
        UniqueConstraint(
            "regulation_id", "entity_type", "content_type",
            name="uq_discovery_source_reg_entity_content",
        ),
    )
