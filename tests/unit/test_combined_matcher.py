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
    session.add(
        RegulationAlias(regulation_id=reg.regulation_id, pattern=alias, kind="REGEX")
    )
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
    refs = matcher.match(
        "Long text without a literal match but the amendment intends to touch it."
    )

    assert len(refs) == 1
    assert refs[0].regulation_id == rid
    assert refs[0].method == "OLLAMA_REFERENCE"
