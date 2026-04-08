from pathlib import Path
from textwrap import dedent

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    Entity,
    Regulation,
    RegulationAlias,
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
