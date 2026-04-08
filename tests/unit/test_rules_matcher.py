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
