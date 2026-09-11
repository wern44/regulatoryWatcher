"""IctChangesService: the dashboard's latest ICT changes."""
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    Base,
    DocumentAnalysis,
    DocumentAnalysisStatus,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationLifecycleLink,
    RegulationType,
)
from regwatch.services.ict_changes import IctChangesService

TODAY = date(2026, 9, 11)


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return Session(engine)


def _add(
    session: Session,
    ref: str,
    published: date | None,
    *,
    is_ict: bool = True,
    title: str | None = None,
    reg_type: RegulationType = RegulationType.CSSF_CIRCULAR,
    lifecycle: LifecycleStage = LifecycleStage.IN_FORCE,
) -> Regulation:
    reg = Regulation(
        type=reg_type,
        reference_number=ref,
        title=title or f"Circular {ref} on ICT things",
        issuing_authority="CSSF",
        publication_date=published,
        lifecycle_stage=lifecycle,
        is_ict=is_ict,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()
    return reg


def _amends(session: Session, child: Regulation, parent: Regulation) -> None:
    session.add(RegulationLifecycleLink(
        from_regulation_id=child.regulation_id,
        to_regulation_id=parent.regulation_id,
        relation="AMENDS",
    ))


def test_lists_recent_ict_changes_newest_first(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add(session, "CSSF 26/910", date(2026, 7, 1))
    _add(session, "CSSF 26/915", date(2026, 8, 27))
    _add(session, "CSSF 26/914", date(2026, 8, 1), is_ict=False)
    _add(session, "CSSF 26/909", date(2026, 8, 2), lifecycle=LifecycleStage.REPEALED)
    session.commit()

    changes = IctChangesService(session).latest(today=TODAY, min_items=0)

    assert [c.reference_number for c in changes] == ["CSSF 26/915", "CSSF 26/910"]
    assert changes[0].days_ago == 15
    assert changes[0].is_recent
    assert changes[0].kind == "New circular"
    assert changes[0].headline == "On ICT things"


def test_amendment_of_an_ict_circular_counts_and_names_its_target(
    tmp_path: Path,
) -> None:
    """The amendment itself isn't flagged ICT, but it changes an ICT circular."""
    session = _session(tmp_path)
    parent = _add(session, "CSSF 22/811", date(2022, 5, 16),
                  title="Circular CSSF 22/811 Authorisation of support PFS")
    child = _add(session, "CSSF 25/900", date(2026, 8, 1), is_ict=False,
                 title="Circular CSSF 25/900 amending Circular CSSF 22/811.")
    _amends(session, child, parent)
    session.commit()

    changes = IctChangesService(session).latest(today=TODAY, min_items=0)

    assert [c.reference_number for c in changes] == ["CSSF 25/900"]
    assert changes[0].kind == "Amendment"
    assert changes[0].headline == "Amending Circular CSSF 22/811."
    assert [(a.reference_number, a.title) for a in changes[0].amends] == [
        ("CSSF 22/811", "Circular CSSF 22/811 Authorisation of support PFS")
    ]


def test_tops_up_with_older_changes(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _add(session, "CSSF 26/915", date(2026, 8, 27))
    _add(session, "CSSF 25/893", date(2025, 5, 28))
    _add(session, "CSSF 24/847", date(2024, 1, 5))
    _add(session, "CSSF 23/833", date(2023, 5, 16))
    session.commit()

    changes = IctChangesService(session).latest(today=TODAY, min_items=3)

    assert [(c.reference_number, c.is_recent) for c in changes] == [
        ("CSSF 26/915", True), ("CSSF 25/893", False), ("CSSF 24/847", False),
    ]


def test_summary_comes_from_the_latest_successful_analysis(tmp_path: Path) -> None:
    session = _session(tmp_path)
    reg = _add(session, "CSSF 26/915", date(2026, 8, 27))
    version = DocumentVersion(
        regulation_id=reg.regulation_id, version_number=1, is_current=True,
        fetched_at=datetime.now(UTC), source_url="https://example.com",
        content_hash="h",
    )
    run = AnalysisRun(
        status=AnalysisRunStatus.SUCCESS, llm_model="m", triggered_by="USER_UI",
    )
    session.add_all([version, run])
    session.flush()
    session.add(DocumentAnalysis(
        run_id=run.run_id, version_id=version.version_id,
        regulation_id=reg.regulation_id, status=DocumentAnalysisStatus.SUCCESS,
        main_points="['Applies DORA to third-country branches', 'From 2027']",
    ))
    session.commit()

    changes = IctChangesService(session).latest(today=TODAY, min_items=0)

    assert changes[0].summary == "Applies DORA to third-country branches; From 2027"
