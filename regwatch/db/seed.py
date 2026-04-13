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
        # Don't overwrite is_ict if it was already set by discovery or user override
        if reg.source_of_truth == "SEED":
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
            RegulationApplicability(
                regulation_id=reg.regulation_id, authorization_type=t
            )
        )
