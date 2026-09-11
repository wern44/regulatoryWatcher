"""Removing document versions that were only *about* a regulation."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    AnalysisRun,
    AnalysisRunStatus,
    Base,
    DocumentAnalysis,
    DocumentChunk,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
    UpdateEvent,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.services.version_cleanup import prune_versions

EUR_LEX = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32022R2554"


def _session(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "app.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    return Session(engine)


def _version(
    s: Session, reg: Regulation, number: int, *, url: str, title: str | None,
    body: str, manual: bool = False,
) -> DocumentVersion:
    now = datetime.now(UTC)
    content_hash = f"{number:064d}"
    if title is not None:
        s.add(UpdateEvent(
            source="test", source_url=url, title=title, published_at=now,
            fetched_at=now, raw_payload={}, content_hash=content_hash,
            is_ict=False, severity="INFORMATIONAL", review_status="NEW",
        ))
    v = DocumentVersion(
        regulation_id=reg.regulation_id, version_number=number, is_current=False,
        fetched_at=now, source_url=url, content_hash=content_hash, html_text=body,
        pdf_is_protected=False, pdf_manual_upload=manual, change_summary="stale diff",
    )
    s.add(v)
    s.flush()
    return v


def _seed(s: Session) -> Regulation:
    dora = Regulation(
        type=RegulationType.EU_REGULATION, reference_number="Regulation (EU) 2022/2554",
        title="DORA", issuing_authority="EU", lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=True, url=EUR_LEX, celex_id="32022R2554", source_of_truth="SEED",
    )
    s.add(dora)
    s.flush()
    _version(s, dora, 1, url="https://finance.ec.europa.eu/news/a",
             title="Another step closer to DORA", body="news a")
    _version(s, dora, 2, url=EUR_LEX, title="Regulation (EU) 2022/2554 of the EP",
             body="article 1 original")
    news = _version(s, dora, 3, url="https://finance.ec.europa.eu/news/b",
                    title="Have your say on DORA", body="news b")
    _version(s, dora, 4, url="upload://dora.pdf", title=None,
             body="article 1 revised", manual=True)
    news.is_current = True
    s.add(DocumentChunk(
        version_id=news.version_id, regulation_id=dora.regulation_id, chunk_index=0,
        text="news b", token_count=2, lifecycle_stage="IN_FORCE", is_ict=True,
        authorization_types=["AIFM"],
    ))
    run = AnalysisRun(status=AnalysisRunStatus.SUCCESS, queued_version_ids=[news.version_id],
                      started_at=datetime.now(UTC), llm_model="m", triggered_by="TEST")
    s.add(run)
    s.flush()
    s.add(DocumentAnalysis(run_id=run.run_id, version_id=news.version_id,
                           regulation_id=dora.regulation_id, status="SUCCESS"))
    s.commit()
    return dora


def test_dry_run_reports_and_changes_nothing(tmp_path: Path) -> None:
    s = _session(tmp_path)
    _seed(s)

    report = prune_versions(s, apply=False)

    assert report.removed == 2
    assert report.kept == 2
    assert report.analyses_removed == 1
    assert report.by_regulation == {"Regulation (EU) 2022/2554": 2}
    assert s.query(DocumentVersion).count() == 4


def test_apply_keeps_the_regulations_own_texts_renumbered(tmp_path: Path) -> None:
    s = _session(tmp_path)
    dora = _seed(s)

    prune_versions(s, apply=True)
    s.commit()

    versions = (
        s.query(DocumentVersion)
        .filter_by(regulation_id=dora.regulation_id)
        .order_by(DocumentVersion.version_number)
        .all()
    )
    assert [(v.version_number, v.source_url) for v in versions] == [
        (1, EUR_LEX), (2, "upload://dora.pdf"),
    ]
    assert [v.is_current for v in versions] == [False, True]
    assert versions[0].change_summary is None
    assert "+article 1 revised" in (versions[1].change_summary or "")
    assert s.query(DocumentChunk).count() == 0
    assert s.query(DocumentAnalysis).count() == 0
    s.execute(text(
        "INSERT INTO document_chunk_fts(document_chunk_fts, rank) VALUES ('integrity-check', 1)"
    ))


def test_renumbering_does_not_collide_when_ids_are_out_of_order(tmp_path: Path) -> None:
    s = _session(tmp_path)
    dora = _seed(s)
    # The kept versions' numbers run against their ids: renumbering in id
    # order would move the EUR-Lex text onto the upload's number 2.
    rows = s.query(DocumentVersion).order_by(DocumentVersion.version_id).all()
    for v in rows:
        v.version_number += 10
        s.flush()
    rows[3].version_number = 2  # upload, highest id
    s.flush()
    rows[1].version_number = 3  # EUR-Lex text, lower id
    s.commit()

    prune_versions(s, apply=True)
    s.commit()

    numbers = [
        v.version_number
        for v in s.query(DocumentVersion).filter_by(regulation_id=dora.regulation_id)
    ]
    assert sorted(numbers) == [1, 2]
