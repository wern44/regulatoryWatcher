from datetime import UTC, datetime
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base, DocumentVersion, LifecycleStage, Regulation, RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.services.upload import index_uploaded_version


def test_index_uploaded_version_skips_protected(tmp_path):
    engine = create_app_engine(tmp_path / "db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="X", title="t",
            issuing_authority="CSSF", lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text="blah blah ICT",
            pdf_is_protected=True,
        )
        s.add(v); s.commit()

        llm = MagicMock()
        llm.embed.return_value = [0.0, 0.0, 0.0, 0.0]
        count = index_uploaded_version(
            session=s, version_id=v.version_id, llm=llm,
            chunk_size_tokens=100, overlap_tokens=10, authorization_types=[],
        )
        assert count == 0
        llm.embed.assert_not_called()


def test_index_uploaded_version_indexes_normal_document(tmp_path):
    engine = create_app_engine(tmp_path / "db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    with Session(engine) as s:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR, reference_number="X", title="t",
            issuing_authority="CSSF", lifecycle_stage=LifecycleStage.IN_FORCE,
            url="x", source_of_truth="SEED",
        )
        s.add(reg); s.flush()
        v = DocumentVersion(
            regulation_id=reg.regulation_id, version_number=1, is_current=True,
            fetched_at=datetime.now(UTC), source_url="x", content_hash="h",
            pdf_extracted_text=(
                "ICT document with substantial text about risk management "
                "and DORA compliance."
            ),
        )
        s.add(v); s.commit()

        llm = MagicMock()
        llm.embed.return_value = [0.0, 0.0, 0.0, 0.0]
        count = index_uploaded_version(
            session=s, version_id=v.version_id, llm=llm,
            chunk_size_tokens=100, overlap_tokens=10, authorization_types=["AIFM"],
        )
        assert count > 0
        assert llm.embed.call_count == count
