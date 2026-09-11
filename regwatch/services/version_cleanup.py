"""Remove document versions that were only *about* their regulation.

Until the pipeline learnt to tell a regulation's own text from a document
that merely mentions it (``regwatch.pipeline.persist.text_of``), every
matched news item, FAQ or consultation became a new version: DORA collected
183 of them and its "current text" was a news page. This keeps a version
only when it is the regulation's own text -- by the same rule the pipeline
now applies -- or a manual upload, then renumbers what is left, recomputes
the diffs between the kept versions and marks the latest one current.
Chunks and analyses of removed versions go with them (FK cascade).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from regwatch.db.models import (
    DocumentAnalysis,
    DocumentVersion,
    Regulation,
    UpdateEvent,
)
from regwatch.domain.types import RawDocument
from regwatch.pipeline.diff import compute_diff
from regwatch.pipeline.persist import text_of


@dataclass
class PruneReport:
    kept: int = 0
    removed: int = 0
    analyses_removed: int = 0
    by_regulation: dict[str, int] = field(default_factory=dict)  # ref -> removed


def prune_versions(session: Session, *, apply: bool) -> PruneReport:
    """Report (``apply=False``) or delete the versions that aren't the
    regulation's own text. The caller commits."""
    titles: dict[str, str] = {
        h: t for h, t in session.execute(select(UpdateEvent.content_hash, UpdateEvent.title))
    }
    refs: dict[int, str] = {
        rid: ref
        for rid, ref in session.execute(
            select(Regulation.regulation_id, Regulation.reference_number)
        )
    }
    report = PruneReport()
    removed: Counter[str] = Counter()
    doomed: list[int] = []
    kept_by_reg: dict[int, list[DocumentVersion]] = {}

    for version in session.scalars(
        select(DocumentVersion).order_by(
            DocumentVersion.regulation_id, DocumentVersion.version_number
        )
    ):
        raw = RawDocument(
            source="",
            source_url=version.source_url or "",
            title=titles.get(version.content_hash, ""),
            published_at=version.fetched_at,
            raw_payload={},
            fetched_at=version.fetched_at or datetime.now(UTC),
        )
        own_text = version.pdf_manual_upload or text_of(
            session, raw, [version.regulation_id]
        )
        if own_text:
            kept_by_reg.setdefault(version.regulation_id, []).append(version)
        else:
            doomed.append(version.version_id)
            removed[refs[version.regulation_id]] += 1

    report.kept = sum(len(vs) for vs in kept_by_reg.values())
    report.removed = len(doomed)
    report.by_regulation = dict(removed.most_common())
    report.analyses_removed = session.scalar(
        select(func.count()).select_from(DocumentAnalysis).where(
            DocumentAnalysis.version_id.in_(doomed)
        )
    ) or 0
    if not apply or not doomed:
        return report

    # Vector rows live in a virtual table without a foreign key.
    for version_id in doomed:
        session.execute(
            sa_text(
                "DELETE FROM document_chunk_vec WHERE chunk_id IN "
                "(SELECT chunk_id FROM document_chunk WHERE version_id = :vid)"
            ),
            {"vid": version_id},
        )
    session.query(DocumentVersion).filter(
        DocumentVersion.version_id.in_(doomed)
    ).delete(synchronize_session=False)
    session.flush()

    renumber = [
        versions for regulation_id, versions in kept_by_reg.items()
        if refs[regulation_id] in removed
    ]
    # Park the numbers first: (regulation_id, version_number) is unique.
    for versions in renumber:
        for version in versions:
            version.version_number = -version.version_number
    session.flush()
    for versions in renumber:
        previous_text = ""
        for number, version in enumerate(versions, start=1):
            body = version.pdf_extracted_text or version.html_text or ""
            version.version_number = number
            version.change_summary = (
                compute_diff(previous_text, body) if previous_text else None
            )
            version.is_current = number == len(versions)
            previous_text = body
    session.flush()
    return report
