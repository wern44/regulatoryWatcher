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
    CSSF_CIRCULAR = "CSSF_CIRCULAR"
    CSSF_REGULATION = "CSSF_REGULATION"
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
