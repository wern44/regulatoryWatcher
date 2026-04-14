"""CSSF website discovery orchestrator.

Runs the scraper per configured authorization type, reconciles results with the
catalog, writes DiscoveryRun + DiscoveryRunItem rows with per-reg outcomes,
manages amendment graph via RegulationLifecycleLink, and respects existing
RegulationOverride precedence.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from regwatch.config import CssfDiscoveryConfig
from regwatch.db.models import (
    AuthorizationType,
    DiscoveryRun,
    DiscoveryRunItem,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationLifecycleLink,
    RegulationOverride,
    RegulationType,
)
from regwatch.discovery.cssf_scraper import (
    CircularDetail,
    CircularListingRow,
    CircularNotFoundError,
    fetch_circular_detail,
    list_circulars,
)
from regwatch.discovery.heuristics import is_ict_by_heuristic

logger = logging.getLogger(__name__)

CSSF_ENTITY_SLUGS: dict[AuthorizationType, str] = {
    AuthorizationType.AIFM: "aifms",
    AuthorizationType.CHAPTER15_MANCO: "management-companies-chapter-15",
}


class CssfDiscoveryService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        config: CssfDiscoveryConfig,
        http_client: httpx.Client | None = None,
        on_progress: Callable[..., None] | None = None,
    ) -> None:
        self._sf = session_factory
        self._config = config
        self._client = http_client
        self._on_progress = on_progress or (lambda **_: None)

    def run(
        self,
        *,
        entity_types: list[AuthorizationType],
        mode: Literal["full", "incremental"],
        triggered_by: str,
    ) -> int:
        with self._sf() as s:
            run = DiscoveryRun(
                status="RUNNING",
                started_at=datetime.now(UTC),
                triggered_by=triggered_by,
                entity_types=[et.value for et in entity_types],
                mode=mode,
            )
            s.add(run)
            s.commit()
            run_id = run.run_id

        aggregate_error: str | None = None
        try:
            for et in entity_types:
                slug = CSSF_ENTITY_SLUGS.get(et)
                if slug is None:
                    logger.warning("no slug mapped for %s; skipping", et.value)
                    continue
                try:
                    self._run_for_slug(run_id, et, slug, mode)
                except Exception as e:  # noqa: BLE001
                    logger.exception("slug %s failed", slug)
                    aggregate_error = (
                        f"{aggregate_error}\n{slug}: {e}" if aggregate_error else f"{slug}: {e}"
                    )
        finally:
            self._finalize_run(run_id, aggregate_error)

        return run_id

    def _run_for_slug(
        self, run_id: int, auth_type: AuthorizationType, slug: str, mode: str
    ) -> None:
        total = 0
        self._on_progress(total_scraped=0, entity_type=auth_type.value)
        for row in list_circulars(
            slug,
            client=self._client,
            request_delay_ms=self._config.request_delay_ms,
        ):
            total += 1
            self._on_progress(total_scraped=total, reference=row.reference_number)
            if mode == "incremental" and self._reference_exists(row.reference_number):
                break
            outcome = self._reconcile_row(run_id, auth_type, row)
            logger.info("row %s -> %s", row.reference_number, outcome)

    def _reconcile_row(
        self, run_id: int, auth_type: AuthorizationType, listing: CircularListingRow
    ) -> str:
        with self._sf() as s:
            override = s.scalar(
                select(RegulationOverride).where(
                    RegulationOverride.reference_number == listing.reference_number,
                    RegulationOverride.action == "EXCLUDE",
                )
            )
            if override is not None:
                self._write_item(
                    run_id, None, listing.reference_number, "UNCHANGED",
                    listing.detail_url, [auth_type.value],
                    note="excluded by RegulationOverride",
                )
                return "UNCHANGED"

        try:
            detail = fetch_circular_detail(
                listing.detail_url,
                client=self._client,
                request_delay_ms=self._config.request_delay_ms,
            )
        except CircularNotFoundError:
            return self._handle_withdrawal(run_id, auth_type, listing)
        except Exception as e:  # noqa: BLE001
            logger.warning("detail fetch failed for %s: %s", listing.reference_number, e)
            self._write_item(
                run_id, None, listing.reference_number, "FAILED",
                listing.detail_url, [auth_type.value],
                note=f"detail fetch failed: {e}",
            )
            return "FAILED"

        # Re-check the override using the detail's canonical reference, which
        # may differ from the listing row's ref (e.g. when the listing title
        # and detail page disagree, or when redirects consolidate refs).
        if detail.reference_number and detail.reference_number != listing.reference_number:
            with self._sf() as s:
                override2 = s.scalar(
                    select(RegulationOverride).where(
                        RegulationOverride.reference_number == detail.reference_number,
                        RegulationOverride.action == "EXCLUDE",
                    )
                )
                if override2 is not None:
                    self._write_item(
                        run_id, None, detail.reference_number, "UNCHANGED",
                        listing.detail_url, [auth_type.value],
                        note="excluded by RegulationOverride",
                    )
                    return "UNCHANGED"

        with self._sf() as s:
            existing = s.scalar(
                select(Regulation).where(Regulation.reference_number == detail.reference_number)
            )
            if existing is None:
                reg = self._create_regulation(s, detail, listing, auth_type)
                self._ensure_amendment_stubs(s, detail)
                self._sync_lifecycle_links(s, reg, detail)
                s.commit()
                self._write_item(
                    run_id, reg.regulation_id, detail.reference_number, "NEW",
                    listing.detail_url, [auth_type.value], note=None,
                )
                return "NEW"

            self._ensure_applicability(s, existing, auth_type)
            current = self._current_amendment_links(s, existing)
            incoming = set(detail.amended_by_refs)
            amended = incoming - current["AMENDED_BY"]

            if amended:
                self._ensure_amendment_stubs(s, detail)
                self._sync_lifecycle_links(s, existing, detail)
                self._refresh_metadata(existing, detail, listing)
                s.commit()
                self._write_item(
                    run_id, existing.regulation_id, detail.reference_number, "AMENDED",
                    listing.detail_url, [auth_type.value],
                    note=f"new amendments: {sorted(amended)}",
                )
                return "AMENDED"

            changed = self._refresh_metadata(existing, detail, listing)
            if changed:
                s.commit()
                self._write_item(
                    run_id, existing.regulation_id, detail.reference_number, "UPDATED_METADATA",
                    listing.detail_url, [auth_type.value], note=None,
                )
                return "UPDATED_METADATA"

            s.commit()
            self._write_item(
                run_id, existing.regulation_id, detail.reference_number, "UNCHANGED",
                listing.detail_url, [auth_type.value], note=None,
            )
            return "UNCHANGED"

    # ----- helpers -----

    def _reference_exists(self, ref: str) -> bool:
        with self._sf() as s:
            return s.scalar(
                select(Regulation.regulation_id).where(Regulation.reference_number == ref)
            ) is not None

    def _handle_withdrawal(
        self, run_id: int, auth_type: AuthorizationType, listing: CircularListingRow
    ) -> str:
        with self._sf() as s:
            existing = s.scalar(
                select(Regulation).where(Regulation.reference_number == listing.reference_number)
            )
            if existing is not None:
                existing.lifecycle_stage = LifecycleStage.REPEALED
                s.commit()
                self._write_item(
                    run_id, existing.regulation_id, listing.reference_number, "WITHDRAWN",
                    listing.detail_url, [auth_type.value], note="detail 404",
                )
                return "WITHDRAWN"
        self._write_item(
            run_id, None, listing.reference_number, "FAILED",
            listing.detail_url, [auth_type.value],
            note="detail 404 and no existing regulation row",
        )
        return "FAILED"

    def _create_regulation(
        self, s: Session, detail: CircularDetail,
        listing: CircularListingRow, auth_type: AuthorizationType,
    ) -> Regulation:
        override = self._ict_override(s, detail.reference_number)
        if override == "SET_ICT":
            is_ict = True
        elif override == "UNSET_ICT":
            is_ict = False
        else:
            is_ict = is_ict_by_heuristic(title=detail.clean_title, description=detail.description)

        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number=detail.reference_number,
            title=detail.clean_title or listing.raw_title,
            issuing_authority="CSSF",
            publication_date=detail.published_at or listing.publication_date,
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=is_ict,
            needs_review=(not is_ict) and (override is None),
            url=detail.pdf_url_en or detail.pdf_url_fr or listing.detail_url,
            source_of_truth="CSSF_WEB",
        )
        s.add(reg)
        s.flush()
        self._ensure_applicability(s, reg, auth_type)
        return reg

    def _ict_override(self, s: Session, ref: str) -> str | None:
        return s.scalar(
            select(RegulationOverride.action).where(
                RegulationOverride.reference_number == ref,
                RegulationOverride.action.in_(["SET_ICT", "UNSET_ICT"]),
            )
        )

    def _ensure_applicability(
        self, s: Session, reg: Regulation, auth_type: AuthorizationType
    ) -> None:
        exists = s.scalar(
            select(RegulationApplicability).where(
                RegulationApplicability.regulation_id == reg.regulation_id,
                RegulationApplicability.authorization_type == auth_type.value,
            )
        )
        if exists is None:
            s.add(RegulationApplicability(
                regulation_id=reg.regulation_id,
                authorization_type=auth_type.value,
            ))
            s.flush()

    def _ensure_amendment_stubs(self, s: Session, detail: CircularDetail) -> None:
        refs = set(detail.amended_by_refs) | set(detail.amends_refs) | set(detail.supersedes_refs)
        for ref in refs:
            if not ref:
                continue
            existing = s.scalar(
                select(Regulation).where(Regulation.reference_number == ref)
            )
            if existing is None:
                s.add(Regulation(
                    type=RegulationType.CSSF_CIRCULAR,
                    reference_number=ref,
                    title=ref,
                    issuing_authority="CSSF",
                    lifecycle_stage=LifecycleStage.IN_FORCE,
                    is_ict=False,
                    needs_review=True,
                    url="",
                    source_of_truth="CSSF_STUB",
                ))
                s.flush()

    def _sync_lifecycle_links(
        self, s: Session, reg: Regulation, detail: CircularDetail
    ) -> None:
        def _ensure_link(from_id: int, to_ref: str, relation: str) -> None:
            to_reg = s.scalar(select(Regulation).where(Regulation.reference_number == to_ref))
            if to_reg is None or to_reg.regulation_id == from_id:
                return
            dup = s.scalar(
                select(RegulationLifecycleLink).where(
                    RegulationLifecycleLink.from_regulation_id == from_id,
                    RegulationLifecycleLink.to_regulation_id == to_reg.regulation_id,
                    RegulationLifecycleLink.relation == relation,
                )
            )
            if dup is None:
                s.add(RegulationLifecycleLink(
                    from_regulation_id=from_id,
                    to_regulation_id=to_reg.regulation_id,
                    relation=relation,
                ))
                s.flush()

        for ref in detail.amended_by_refs:
            to_reg = s.scalar(select(Regulation).where(Regulation.reference_number == ref))
            if to_reg is not None and to_reg.regulation_id != reg.regulation_id:
                _ensure_link(to_reg.regulation_id, detail.reference_number, "AMENDS")
        for ref in detail.amends_refs:
            _ensure_link(reg.regulation_id, ref, "AMENDS")
        for ref in detail.supersedes_refs:
            _ensure_link(reg.regulation_id, ref, "REPEALS")

    def _current_amendment_links(self, s: Session, reg: Regulation) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {"AMENDED_BY": set(), "AMENDS": set(), "REPEALS": set()}
        incoming = s.scalars(
            select(RegulationLifecycleLink).where(
                RegulationLifecycleLink.to_regulation_id == reg.regulation_id,
                RegulationLifecycleLink.relation == "AMENDS",
            )
        ).all()
        for link in incoming:
            from_reg = s.get(Regulation, link.from_regulation_id)
            if from_reg is not None:
                result["AMENDED_BY"].add(from_reg.reference_number)
        outgoing = s.scalars(
            select(RegulationLifecycleLink).where(
                RegulationLifecycleLink.from_regulation_id == reg.regulation_id,
            )
        ).all()
        for link in outgoing:
            to_reg = s.get(Regulation, link.to_regulation_id)
            if to_reg is None:
                continue
            if link.relation == "AMENDS":
                result["AMENDS"].add(to_reg.reference_number)
            elif link.relation == "REPEALS":
                result["REPEALS"].add(to_reg.reference_number)
        return result

    def _refresh_metadata(
        self, reg: Regulation, detail: CircularDetail, listing: CircularListingRow
    ) -> bool:
        changed = False
        new_title = detail.clean_title or listing.raw_title
        if new_title and reg.title != new_title and reg.source_of_truth != "SEED":
            reg.title = new_title
            changed = True
        new_url = detail.pdf_url_en or detail.pdf_url_fr or listing.detail_url
        if new_url and reg.url != new_url and reg.source_of_truth != "SEED":
            reg.url = new_url
            changed = True
        return changed

    def _write_item(
        self, run_id: int, regulation_id: int | None,
        reference_number: str, outcome: str,
        detail_url: str | None, entity_types: list[str], note: str | None,
    ) -> None:
        with self._sf() as s:
            s.add(DiscoveryRunItem(
                run_id=run_id,
                regulation_id=regulation_id,
                reference_number=reference_number,
                outcome=outcome,
                detail_url=detail_url,
                entity_types=entity_types,
                note=note,
            ))
            s.commit()

    def _finalize_run(self, run_id: int, error: str | None) -> None:
        with self._sf() as s:
            run = s.get(DiscoveryRun, run_id)
            if run is None:
                return
            run.finished_at = datetime.now(UTC)
            items = s.scalars(
                select(DiscoveryRunItem).where(DiscoveryRunItem.run_id == run_id)
            ).all()
            run.total_scraped = len(items)
            counts: dict[str, int] = {}
            for i in items:
                counts[i.outcome] = counts.get(i.outcome, 0) + 1
            run.new_count = counts.get("NEW", 0)
            run.amended_count = counts.get("AMENDED", 0)
            run.updated_count = counts.get("UPDATED_METADATA", 0)
            run.unchanged_count = counts.get("UNCHANGED", 0)
            run.withdrawn_count = counts.get("WITHDRAWN", 0)
            run.failed_count = counts.get("FAILED", 0)
            run.error_summary = error
            ok_count = run.new_count + run.amended_count + run.updated_count + run.unchanged_count
            if error and run.failed_count > 0 and ok_count > 0:
                run.status = "PARTIAL"
            elif error or (run.failed_count > 0 and ok_count == 0):
                run.status = "FAILED"
            elif run.failed_count > 0:
                run.status = "PARTIAL"
            else:
                run.status = "SUCCESS"
            s.commit()
