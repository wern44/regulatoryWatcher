"""Routes for the CSSF discovery run progress + result + history pages."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Request
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from regwatch.db.models import DiscoveryRun, DiscoveryRunItem

router = APIRouter()


@router.get("/discovery/runs")
def runs_list(request: Request):
    sf = request.app.state.session_factory
    with sf() as s:
        runs = s.scalars(
            select(DiscoveryRun).order_by(desc(DiscoveryRun.started_at)).limit(50)
        ).all()
        dto_list = [_to_summary(r) for r in runs]
    return request.app.state.templates.TemplateResponse(
        request,
        "discovery/list.html",
        {"runs": dto_list, "active": "catalog"},
    )


@router.get("/discovery/runs/{run_id}")
def run_page(request: Request, run_id: int):
    sf = request.app.state.session_factory
    with sf() as s:
        run_info = _load_run(s, run_id)
    cell_breakdown = _compute_cell_breakdown(run_info["items"]) if run_info else []
    retired_count = run_info.get("retired_count", 0) if run_info else 0
    return request.app.state.templates.TemplateResponse(
        request,
        "discovery/run.html",
        {
            "run": run_info,
            "progress": request.app.state.cssf_discovery_progress,
            "run_id": run_id,
            "active": "catalog",
            "cell_breakdown": cell_breakdown,
            "retired_count": retired_count,
        },
    )


@router.get("/discovery/runs/{run_id}/status")
def run_status_fragment(request: Request, run_id: int):
    sf = request.app.state.session_factory
    with sf() as s:
        run_info = _load_run(s, run_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "discovery/_run_status.html",
        {
            "run": run_info,
            "progress": request.app.state.cssf_discovery_progress,
            "run_id": run_id,
        },
    )


# ----- helpers -----


def _to_summary(run: DiscoveryRun) -> dict:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "triggered_by": run.triggered_by,
        "entity_types": run.entity_types or [],
        "mode": run.mode,
        "total_scraped": run.total_scraped,
        "new_count": run.new_count,
        "amended_count": run.amended_count,
        "updated_count": run.updated_count,
        "unchanged_count": run.unchanged_count,
        "withdrawn_count": run.withdrawn_count,
        "failed_count": run.failed_count,
        "retired_count": run.retired_count or 0,
        "error_summary": run.error_summary,
    }


def _compute_cell_breakdown(items: list[dict]) -> list[dict]:
    """Group items by (entity_type, content_type) and count outcomes per cell."""
    groups: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"new": 0, "amended": 0, "updated": 0,
                 "unchanged": 0, "failed": 0, "retired": 0}
    )
    outcome_map = {
        "NEW": "new",
        "AMENDED": "amended",
        "UPDATED_METADATA": "updated",
        "UNCHANGED": "unchanged",
        "FAILED": "failed",
        "RETIRED": "retired",
    }
    for it in items:
        key = (it.get("entity_type") or "", it.get("content_type") or "")
        col = outcome_map.get(it.get("outcome", ""))
        if col is not None:
            groups[key][col] += 1
    return [
        {"entity_type": et, "content_type": ct, **counts}
        for (et, ct), counts in sorted(groups.items())
    ]


def _load_run(s: Session, run_id: int) -> dict | None:
    run = s.get(DiscoveryRun, run_id)
    if run is None:
        return None
    items = s.scalars(
        select(DiscoveryRunItem)
        .where(DiscoveryRunItem.run_id == run_id)
        .order_by(DiscoveryRunItem.created_at)
    ).all()
    item_dtos = [
        {
            "item_id": i.item_id,
            "reference_number": i.reference_number,
            "outcome": i.outcome,
            "regulation_id": i.regulation_id,
            "detail_url": i.detail_url,
            "entity_type": i.entity_type,
            "content_type": i.content_type,
            "note": i.note,
        }
        for i in items
    ]
    dto = _to_summary(run)
    dto["items"] = item_dtos
    return dto
