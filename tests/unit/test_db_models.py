from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Authorization,
    AuthorizationType,
    Base,
    DocumentChunk,
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
        fetched_at=datetime.now(UTC),
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
        published_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
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


def _make_regulation(session: Session) -> Regulation:
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
    return reg


def _make_version(
    session: Session, reg: Regulation, *, version_number: int = 1, is_current: bool = True
) -> DocumentVersion:
    v = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=version_number,
        is_current=is_current,
        fetched_at=datetime.now(UTC),
        source_url="https://example.com/v1",
        content_hash=("a" * 63 + str(version_number)),
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(v)
    session.flush()
    return v


def test_delete_regulation_cascades_to_chunks_and_versions(tmp_path: Path) -> None:
    session = _fresh_session(tmp_path)
    reg = _make_regulation(session)
    v = _make_version(session, reg)

    chunk = DocumentChunk(
        version_id=v.version_id,
        regulation_id=reg.regulation_id,
        chunk_index=0,
        text="DORA ICT risk management",
        token_count=5,
        lifecycle_stage="IN_FORCE",
        is_ict=True,
        authorization_types=[],
    )
    session.add(chunk)

    ev = UpdateEvent(
        source="CSSF_RSS",
        source_url="https://example.com/ev",
        title="Event",
        published_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
        raw_payload={},
        content_hash="c" * 64,
        severity="INFORMATIONAL",
        review_status="NEW",
    )
    ev.regulation_links.append(
        UpdateEventRegulationLink(
            regulation_id=reg.regulation_id,
            match_method="REGEX_ALIAS",
            confidence=1.0,
        )
    )
    session.add(ev)
    session.commit()

    session.delete(reg)
    session.commit()

    assert session.query(Regulation).count() == 0
    assert session.query(DocumentVersion).count() == 0
    assert session.query(DocumentChunk).count() == 0
    assert session.query(UpdateEventRegulationLink).count() == 0


def test_datetime_round_trip_timezone_aware(tmp_path: Path) -> None:
    session = _fresh_session(tmp_path)
    reg = _make_regulation(session)
    v = _make_version(session, reg)
    session.commit()

    session.expire(v)
    loaded = session.get(DocumentVersion, v.version_id)
    assert loaded is not None
    assert loaded.fetched_at.tzinfo is not None
    # Must not raise TypeError on comparison
    delta = datetime.now(UTC) - loaded.fetched_at
    assert delta.total_seconds() >= 0


def test_is_current_uniqueness_enforced(tmp_path: Path) -> None:
    session = _fresh_session(tmp_path)
    reg = _make_regulation(session)
    _make_version(session, reg, version_number=1, is_current=True)
    session.commit()

    # Second version also is_current=True for the same regulation — must fail
    v2 = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=2,
        is_current=True,
        fetched_at=datetime.now(UTC),
        source_url="https://example.com/v2",
        content_hash="b" * 64,
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(v2)
    with pytest.raises(IntegrityError):
        session.commit()
