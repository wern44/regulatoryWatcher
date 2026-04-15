"""CSSF website discovery orchestrator.

Runs the scraper per configured authorization type, reconciles results with the
catalog, writes DiscoveryRun + DiscoveryRunItem rows with per-reg outcomes,
manages amendment graph via RegulationLifecycleLink, and respects existing
RegulationOverride precedence.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from regwatch.config import CssfDiscoveryConfig, PublicationTypeConfig
from regwatch.db.models import (
    AuthorizationType,
    DiscoveryRun,
    DiscoveryRunItem,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
    RegulationDiscoverySource,
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


@dataclass
class DiscoverySourceDTO:
    entity_type: str
    content_type: str
    first_seen_at: datetime
    last_seen_at: datetime
    first_seen_run_id: int
    last_seen_run_id: int


@dataclass
class RetirePreview:
    candidates: list[str]          # refs that would match the retire query
    would_retire: bool             # False if tripwire would fire
    tripwire_reason: str | None    # human-readable if would_retire is False
    total_scraped: int             # for transparency


# CSSF detail pages list applicable entities using these human-readable labels.
# Map them to our AuthorizationType enum. Substring-match: the label as it appears
# in ``.entities-list li`` on CSSF detail pages is checked for any of these prefixes.
CSSF_ENTITY_LABEL_TO_AUTH: dict[str, AuthorizationType] = {
    "Alternative investment fund manager": AuthorizationType.AIFM,
    "AIFM": AuthorizationType.AIFM,
    "UCITS management company": AuthorizationType.CHAPTER15_MANCO,
    "UCITS management companies": AuthorizationType.CHAPTER15_MANCO,
    "Chapter 15 management company": AuthorizationType.CHAPTER15_MANCO,
    "Chapter 15 management companies": AuthorizationType.CHAPTER15_MANCO,
    "Management company": AuthorizationType.CHAPTER15_MANCO,
}


def _map_labels_to_auth_types(labels: list[str]) -> list[AuthorizationType]:
    """Match each label against the known prefix mapping; return deduped list."""
    found: set[AuthorizationType] = set()
    for label in labels:
        norm = label.strip()
        for prefix, auth in CSSF_ENTITY_LABEL_TO_AUTH.items():
            if prefix.lower() in norm.lower():
                found.add(auth)
    return sorted(found, key=lambda a: a.value)


def _compose_title(detail: CircularDetail, listing: CircularListingRow) -> str:
    """Build the stored regulation title.

    Prefer the detail page's ``clean_title`` when it carries information
    beyond the bare reference number; otherwise combine the reference with
    the subtitle (``detail.description``) so downstream pages read
    "Circular CSSF 25/896 on outsourcing arrangements" instead of the
    bare ref.
    """
    bare = (detail.clean_title or "").strip()
    ref = (detail.reference_number or "").strip()
    listing_raw = (listing.raw_title or "").strip()

    bare_is_just_ref = False
    if ref:
        bare_is_just_ref = (
            bare.lower() == f"circular {ref}".lower()
            or bare == ref
        )

    if bare and not bare_is_just_ref:
        return bare

    subtitle = (detail.description or "").strip()
    if ref and subtitle:
        return f"Circular {ref} {subtitle}".strip()
    # No reference number available: fall back to the listing title without
    # prefixing so we don't end up with "Circular  <title>".
    if not ref:
        if bare:
            return bare
        if listing_raw:
            return listing_raw
        return ""
    if listing_raw and listing_raw != bare:
        return listing_raw
    return bare or ref


def _slug_from_reference(ref: str) -> str | None:
    """Convert ``'CSSF 22/806'`` to ``'circular-cssf-22-806'``.

    Returns ``None`` if the reference does not fit the expected shape
    (``<PREFIX> NN/NNN`` where ``PREFIX`` is ``CSSF``, ``CSSF-SOMETHING``,
    ``IML`` or similar).
    """
    m = re.match(r"^([A-Z]+(?:-[A-Z]+)?)\s+(\d+)/(\d+)$", ref.strip())
    if not m:
        return None
    prefix = m.group(1).lower()
    return f"circular-{prefix}-{m.group(2)}-{m.group(3)}"


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
        # Defaults set here so instance methods never AttributeError before
        # run() is called.
        self._dry_run: bool = False
        self._restrict_pub_slug: str | None = None

    def list_discovery_sources(self, regulation_id: int) -> list[DiscoverySourceDTO]:
        """Return all (entity, content_type) cells where this regulation was seen."""
        with self._sf() as s:
            rows = s.scalars(
                select(RegulationDiscoverySource)
                .where(RegulationDiscoverySource.regulation_id == regulation_id)
                .order_by(
                    RegulationDiscoverySource.entity_type,
                    RegulationDiscoverySource.content_type,
                )
            ).all()
            return [
                DiscoverySourceDTO(
                    entity_type=r.entity_type,
                    content_type=r.content_type,
                    first_seen_at=r.first_seen_at,
                    last_seen_at=r.last_seen_at,
                    first_seen_run_id=r.first_seen_run_id,
                    last_seen_run_id=r.last_seen_run_id,
                )
                for r in rows
            ]

    def run(
        self,
        *,
        entity_types: list[AuthorizationType],
        mode: Literal["full", "incremental"],
        triggered_by: str,
        existing_run_id: int | None = None,
        dry_run: bool = False,
        restrict_pub_slug: str | None = None,
    ) -> int:
        self._dry_run = dry_run
        self._restrict_pub_slug = restrict_pub_slug

        # Resolve the publication-type matrix for this run.
        pubs_to_use = list(self._config.publication_types)
        if restrict_pub_slug is not None:
            pubs_to_use = [
                p for p in pubs_to_use
                if p.label == restrict_pub_slug or p.type == restrict_pub_slug
            ]
            if not pubs_to_use:
                raise ValueError(
                    f"restrict_pub_slug={restrict_pub_slug!r} matched no "
                    f"publication_type in config"
                )

        if existing_run_id is None:
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
        else:
            run_id = existing_run_id
            with self._sf() as s:
                run = s.get(DiscoveryRun, run_id)
                if run is None:
                    raise RuntimeError(f"No DiscoveryRun {run_id}")
                run.status = "RUNNING"
                if run.started_at is None:
                    run.started_at = datetime.now(UTC)
                run.triggered_by = triggered_by
                run.entity_types = [et.value for et in entity_types]
                run.mode = mode
                s.commit()

        aggregate_error: str | None = None
        try:
            for et in entity_types:
                entity_filter_id = self._config.entity_filter_ids.get(et.value)
                if entity_filter_id is None:
                    logger.warning("no filter_id mapped for %s; skipping", et.value)
                    continue
                for pub in pubs_to_use:
                    try:
                        self._run_for_cell(run_id, et, entity_filter_id, pub, mode)
                    except Exception as e:  # noqa: BLE001
                        logger.exception(
                            "cell failed: entity=%s (%d) x content=%s (%d)",
                            et.value, entity_filter_id, pub.label, pub.filter_id,
                        )
                        msg = f"{et.value} x {pub.label}: {e}"
                        aggregate_error = (
                            f"{aggregate_error}\n{msg}" if aggregate_error else msg
                        )
        finally:
            self._finalize_run(run_id, aggregate_error)

        return run_id

    def _run_for_cell(
        self,
        run_id: int,
        auth_type: AuthorizationType,
        entity_filter_id: int,
        pub: PublicationTypeConfig,
        mode: str,
    ) -> None:
        total = 0
        self._on_progress(
            total_scraped=0,
            entity_type=auth_type.value,
            content_type=pub.label,
        )
        for row in list_circulars(
            entity_filter_id=entity_filter_id,
            content_type_filter_id=pub.filter_id,
            publication_type_label=pub.label,
            client=self._client,
            request_delay_ms=self._config.request_delay_ms,
        ):
            total += 1
            self._on_progress(
                total_scraped=total,
                reference=row.reference_number,
                entity_type=auth_type.value,
                content_type=pub.label,
            )
            if mode == "incremental" and self._reference_exists(row.reference_number):
                break
            outcome = self._reconcile_row(run_id, auth_type, pub, row)
            logger.info(
                "cell %s x %s  row %s -> %s",
                auth_type.value, pub.label, row.reference_number, outcome,
            )

    def _reconcile_row(
        self,
        run_id: int,
        auth_type: AuthorizationType,
        pub: PublicationTypeConfig,
        listing: CircularListingRow,
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
                    listing.detail_url, auth_type.value, pub.label,
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
            return self._handle_withdrawal(run_id, auth_type, pub, listing)
        except Exception as e:  # noqa: BLE001
            logger.warning("detail fetch failed for %s: %s", listing.reference_number, e)
            self._write_item(
                run_id, None, listing.reference_number, "FAILED",
                listing.detail_url, auth_type.value, pub.label,
                note=f"detail fetch failed: {e}",
            )
            return "FAILED"

        # The detail page may not have a CSSF/IML ref (e.g. laws, regulations).
        # Use the listing row's (synthesized) ref as the canonical key in that case.
        canonical_ref = detail.reference_number or listing.reference_number

        # Re-check the override using the canonical reference, which
        # may differ from the listing row's ref (e.g. when the listing title
        # and detail page disagree, or when redirects consolidate refs).
        if canonical_ref and canonical_ref != listing.reference_number:
            with self._sf() as s:
                override2 = s.scalar(
                    select(RegulationOverride).where(
                        RegulationOverride.reference_number == canonical_ref,
                        RegulationOverride.action == "EXCLUDE",
                    )
                )
                if override2 is not None:
                    self._write_item(
                        run_id, None, canonical_ref, "UNCHANGED",
                        listing.detail_url, auth_type.value, pub.label,
                        note="excluded by RegulationOverride",
                    )
                    return "UNCHANGED"

        with self._sf() as s:
            existing = s.scalar(
                select(Regulation).where(Regulation.reference_number == canonical_ref)
            )
            if existing is None:
                reg = self._create_regulation(s, detail, listing, auth_type, pub)
                self._ensure_amendment_stubs(s, detail)
                self._sync_lifecycle_links(s, reg, detail)
                if not self._dry_run:
                    s.commit()
                self._write_item(
                    run_id, reg.regulation_id, canonical_ref, "NEW",
                    listing.detail_url, auth_type.value, pub.label, note=None,
                )
                self._upsert_discovery_source(
                    run_id=run_id, regulation_id=reg.regulation_id,
                    entity_type=auth_type.value, content_type=pub.label,
                )
                return "NEW"

            self._ensure_applicability(s, existing, auth_type)

            # Reactivation: a previously-retired row is back in the matrix.
            if (
                existing.source_of_truth == "CSSF_WEB"
                and existing.lifecycle_stage == LifecycleStage.REPEALED
            ):
                existing.lifecycle_stage = LifecycleStage.IN_FORCE

            current = self._current_amendment_links(s, existing)
            incoming = set(detail.amended_by_refs)
            amended = incoming - current["AMENDED_BY"]

            if amended:
                self._ensure_amendment_stubs(s, detail)
                self._sync_lifecycle_links(s, existing, detail)
                self._refresh_metadata(existing, detail, listing)
                if not self._dry_run:
                    s.commit()
                self._write_item(
                    run_id, existing.regulation_id, canonical_ref, "AMENDED",
                    listing.detail_url, auth_type.value, pub.label,
                    note=f"new amendments: {sorted(amended)}",
                )
                self._upsert_discovery_source(
                    run_id=run_id, regulation_id=existing.regulation_id,
                    entity_type=auth_type.value, content_type=pub.label,
                )
                return "AMENDED"

            changed = self._refresh_metadata(existing, detail, listing)
            if changed:
                if not self._dry_run:
                    s.commit()
                self._write_item(
                    run_id, existing.regulation_id, canonical_ref, "UPDATED_METADATA",
                    listing.detail_url, auth_type.value, pub.label, note=None,
                )
                self._upsert_discovery_source(
                    run_id=run_id, regulation_id=existing.regulation_id,
                    entity_type=auth_type.value, content_type=pub.label,
                )
                return "UPDATED_METADATA"

            if not self._dry_run:
                s.commit()
            self._write_item(
                run_id, existing.regulation_id, canonical_ref, "UNCHANGED",
                listing.detail_url, auth_type.value, pub.label, note=None,
            )
            self._upsert_discovery_source(
                run_id=run_id, regulation_id=existing.regulation_id,
                entity_type=auth_type.value, content_type=pub.label,
            )
            return "UNCHANGED"

    # ----- helpers -----

    def _upsert_discovery_source(
        self,
        *,
        run_id: int,
        regulation_id: int,
        entity_type: str,
        content_type: str,
    ) -> None:
        """UPSERT the (regulation, entity_type, content_type) provenance row."""
        now = datetime.now(UTC)
        with self._sf() as s:
            existing = s.scalar(
                select(RegulationDiscoverySource).where(
                    RegulationDiscoverySource.regulation_id == regulation_id,
                    RegulationDiscoverySource.entity_type == entity_type,
                    RegulationDiscoverySource.content_type == content_type,
                )
            )
            if existing is None:
                s.add(RegulationDiscoverySource(
                    regulation_id=regulation_id,
                    entity_type=entity_type,
                    content_type=content_type,
                    first_seen_run_id=run_id,
                    first_seen_at=now,
                    last_seen_run_id=run_id,
                    last_seen_at=now,
                ))
            else:
                existing.last_seen_run_id = run_id
                existing.last_seen_at = now
            if not self._dry_run:
                s.commit()

    def _reference_exists(self, ref: str) -> bool:
        with self._sf() as s:
            return s.scalar(
                select(Regulation.regulation_id).where(Regulation.reference_number == ref)
            ) is not None

    def _handle_withdrawal(
        self,
        run_id: int,
        auth_type: AuthorizationType,
        pub: PublicationTypeConfig,
        listing: CircularListingRow,
    ) -> str:
        with self._sf() as s:
            existing = s.scalar(
                select(Regulation).where(Regulation.reference_number == listing.reference_number)
            )
            if existing is not None:
                existing.lifecycle_stage = LifecycleStage.REPEALED
                if not self._dry_run:
                    s.commit()
                self._write_item(
                    run_id, existing.regulation_id, listing.reference_number, "WITHDRAWN",
                    listing.detail_url, auth_type.value, pub.label, note="detail 404",
                )
                return "WITHDRAWN"
        self._write_item(
            run_id, None, listing.reference_number, "FAILED",
            listing.detail_url, auth_type.value, pub.label,
            note="detail 404 and no existing regulation row",
        )
        return "FAILED"

    def _create_regulation(
        self,
        s: Session,
        detail: CircularDetail,
        listing: CircularListingRow,
        auth_type: AuthorizationType,
        pub: PublicationTypeConfig,
    ) -> Regulation:
        composed_title = _compose_title(detail, listing)
        canonical_ref = detail.reference_number or listing.reference_number
        override = self._ict_override(s, canonical_ref)
        if override == "SET_ICT":
            is_ict = True
        elif override == "UNSET_ICT":
            is_ict = False
        else:
            is_ict = is_ict_by_heuristic(
                title=composed_title, description=detail.description
            )

        reg = Regulation(
            type=RegulationType(pub.type),
            reference_number=canonical_ref,
            title=composed_title,
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
        new_title = _compose_title(detail, listing)
        if new_title and reg.title != new_title and reg.source_of_truth != "SEED":
            reg.title = new_title
            changed = True
        new_url = detail.pdf_url_en or detail.pdf_url_fr or listing.detail_url
        if new_url and reg.url != new_url and reg.source_of_truth != "SEED":
            reg.url = new_url
            changed = True
        return changed

    def backfill_titles_and_descriptions(
        self,
        *,
        triggered_by: str = "USER_CLI",
    ) -> dict[str, int]:
        """Re-fetch detail pages for every ``CSSF_WEB`` regulation.

        Updates the stored title using :func:`_compose_title` (which now
        incorporates the subtitle) and re-runs the ICT heuristic against
        the richer text. Skips rows whose ``source_of_truth`` is ``SEED``
        (consistent with :meth:`_refresh_metadata`) and rows without a
        derivable detail URL. Returns a counts dict with keys
        ``updated``, ``newly_ict``, ``failed``, ``no_url``.
        """
        del triggered_by  # reserved for future DiscoveryRun bookkeeping
        counts = {"updated": 0, "newly_ict": 0, "failed": 0, "no_url": 0}
        with self._sf() as s:
            regs = s.scalars(
                select(Regulation).where(Regulation.source_of_truth == "CSSF_WEB")
            ).all()
            for reg in regs:
                slug = _slug_from_reference(reg.reference_number)
                if slug is None:
                    counts["no_url"] += 1
                    continue
                detail_url = f"https://www.cssf.lu/en/Document/{slug}/"
                try:
                    detail = fetch_circular_detail(
                        detail_url,
                        client=self._client,
                        request_delay_ms=self._config.request_delay_ms,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "backfill failed for %s: %s", reg.reference_number, e
                    )
                    counts["failed"] += 1
                    continue

                fake_listing = CircularListingRow(
                    reference_number=detail.reference_number,
                    raw_title=detail.clean_title,
                    description=detail.description,
                    publication_date=detail.published_at,
                    detail_url=detail_url,
                )
                new_title = _compose_title(detail, fake_listing)
                if (
                    new_title
                    and reg.title != new_title
                    and reg.source_of_truth != "SEED"
                ):
                    reg.title = new_title
                    counts["updated"] += 1

                if not reg.is_ict:
                    override = self._ict_override(s, reg.reference_number)
                    if override is None and is_ict_by_heuristic(
                        title=new_title or reg.title or "",
                        description=detail.description,
                    ):
                        reg.is_ict = True
                        reg.needs_review = False
                        counts["newly_ict"] += 1
            s.commit()
        return counts

    def reclassify_cssf_web_ict(self) -> dict[str, int]:
        """Re-run the heuristic on every CSSF_WEB regulation and update is_ict.

        Respects :class:`RegulationOverride` (``SET_ICT`` / ``UNSET_ICT`` rows
        are never touched). Unlike :meth:`backfill_titles_and_descriptions`,
        this can flip ``is_ict`` ``True -> False`` when the heuristic no
        longer matches (e.g. after a keyword-list change that tightened
        word-boundary rules). Returns counts of flipped rows.
        """
        counts = {
            "set_true": 0,
            "set_false": 0,
            "skipped_override": 0,
            "unchanged": 0,
        }
        with self._sf() as s:
            regs = s.scalars(
                select(Regulation).where(Regulation.source_of_truth == "CSSF_WEB")
            ).all()
            for reg in regs:
                override = self._ict_override(s, reg.reference_number)
                if override is not None:
                    counts["skipped_override"] += 1
                    continue
                new_is_ict = is_ict_by_heuristic(
                    title=reg.title or "",
                    description="",  # we don't persist description; rely on title
                )
                if new_is_ict == reg.is_ict:
                    counts["unchanged"] += 1
                    continue
                reg.is_ict = new_is_ict
                # If the heuristic is unsure (False), route to LLM via
                # needs_review; if it is now True, no further review needed.
                reg.needs_review = not new_is_ict
                counts["set_true" if new_is_ict else "set_false"] += 1
            s.commit()
        return counts

    def retire_missing(self, run_id: int) -> int:
        """Mark CSSF_WEB regulations not seen in this run as REPEALED.

        Caller MUST gate this on run.status == "SUCCESS" — a PARTIAL or
        FAILED run must not retire. The in-place callers (see
        _finalize_run) do this gating.

        Returns the number retired. Honours RegulationOverride
        (action="KEEP_ACTIVE"). Never touches SEED / DISCOVERED / CSSF_STUB
        rows. Writes one DiscoveryRunItem per retired regulation with
        outcome="RETIRED".
        """
        retired_count = 0
        with self._sf() as s:
            seen_subq = select(RegulationDiscoverySource.regulation_id).where(
                RegulationDiscoverySource.last_seen_run_id == run_id
            )
            keep_active_refs = list(
                s.scalars(
                    select(RegulationOverride.reference_number).where(
                        RegulationOverride.action == "KEEP_ACTIVE"
                    )
                ).all()
            )

            query = select(Regulation).where(
                Regulation.source_of_truth == "CSSF_WEB",
                Regulation.lifecycle_stage != LifecycleStage.REPEALED,
                Regulation.regulation_id.not_in(seen_subq),
            )
            if keep_active_refs:
                query = query.where(Regulation.reference_number.not_in(keep_active_refs))

            stale = list(s.scalars(query).all())
            for reg in stale:
                reg.lifecycle_stage = LifecycleStage.REPEALED
                s.add(DiscoveryRunItem(
                    run_id=run_id,
                    regulation_id=reg.regulation_id,
                    reference_number=reg.reference_number,
                    outcome="RETIRED",
                    detail_url=None,
                    entity_type="",
                    content_type="",
                    note="absent from all filter-matrix cells",
                ))
                retired_count += 1
            s.commit()
        return retired_count

    def preview_retire_candidates(self, run_id: int) -> RetirePreview:
        """Return refs that WOULD be retired + whether the tripwire would fire.

        Does NOT modify the DB. Used by --dry-run to show the user what
        a real run would retire. Applies the same filter as retire_missing
        (exclude KEEP_ACTIVE, exclude non-CSSF_WEB, exclude already-REPEALED).
        """
        with self._sf() as s:
            run = s.get(DiscoveryRun, run_id)
            total_scraped = run.total_scraped if run else 0
            seen_subq = select(RegulationDiscoverySource.regulation_id).where(
                RegulationDiscoverySource.last_seen_run_id == run_id
            )
            keep_active_refs = list(s.scalars(
                select(RegulationOverride.reference_number).where(
                    RegulationOverride.action == "KEEP_ACTIVE"
                )
            ).all())
            query = select(Regulation.reference_number).where(
                Regulation.source_of_truth == "CSSF_WEB",
                Regulation.lifecycle_stage != LifecycleStage.REPEALED,
                Regulation.regulation_id.not_in(seen_subq),
            )
            if keep_active_refs:
                query = query.where(Regulation.reference_number.not_in(keep_active_refs))
            candidates = sorted(s.scalars(query).all())

        floor = self._config.retire_min_scraped
        would_retire = floor <= 0 or total_scraped >= floor
        tripwire_reason = None if would_retire else (
            f"total_scraped={total_scraped} < retire_min_scraped={floor}; "
            "retire would be skipped — likely silent parser breakage."
        )
        return RetirePreview(
            candidates=candidates,
            would_retire=would_retire,
            tripwire_reason=tripwire_reason,
            total_scraped=total_scraped,
        )

    def _write_item(
        self, run_id: int, regulation_id: int | None,
        reference_number: str, outcome: str,
        detail_url: str | None, entity_type: str, content_type: str,
        note: str | None,
    ) -> None:
        with self._sf() as s:
            s.add(DiscoveryRunItem(
                run_id=run_id,
                regulation_id=regulation_id,
                reference_number=reference_number,
                outcome=outcome,
                detail_url=detail_url,
                entity_type=entity_type,
                content_type=content_type,
                note=note,
            ))
            s.commit()  # always commit — audit trail shows would-be outcomes in dry-run

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
            if error and ok_count > 0:
                run.status = "PARTIAL"
            elif error:
                run.status = "FAILED"
            elif run.failed_count > 0 and ok_count == 0:
                run.status = "FAILED"
            elif run.failed_count > 0:
                run.status = "PARTIAL"
            else:
                run.status = "SUCCESS"

            # Commit the status decision so retire_missing can read it in its
            # own session (NullPool: each with-block is a separate connection).
            s.commit()

            # Retire CSSF_WEB regulations absent from this run — only on SUCCESS.
            # A PARTIAL/FAILED run must never wipe the catalog.
            # A dry-run or single-column restriction also skips retire: we
            # can't prove global absence from an incomplete crawl.
            skip_retire = self._dry_run or self._restrict_pub_slug is not None

            # Tripwire: a run that scraped nothing meaningful may indicate a DOM
            # change breaking the parser silently. Refuse to retire on suspicion.
            min_scraped = self._config.retire_min_scraped
            if not skip_retire and min_scraped > 0 and run.total_scraped < min_scraped:
                logger.warning(
                    "Skipping retire: total_scraped=%d below floor %d. "
                    "Possible silent DOM breakage in the scraper.",
                    run.total_scraped, min_scraped,
                )
                skip_retire = True
                if run.error_summary is None:
                    run.error_summary = (
                        f"Retire skipped: scraped {run.total_scraped} < floor {min_scraped}. "
                        "Investigate scraper output before next full run."
                    )

            if run.status == "SUCCESS" and not skip_retire:
                s.commit()
                run.retired_count = self.retire_missing(run_id)
            else:
                run.retired_count = 0

            s.commit()
