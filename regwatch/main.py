"""FastAPI application factory."""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse as StarletteRedirect
from starlette.responses import Response

from regwatch.config import load_config
from regwatch.db.bootstrap import seed_defaults, upgrade_schema
from regwatch.db.engine import create_app_engine
from regwatch.llm.client import LLMClient
from regwatch.pipeline.progress import PipelineProgress
from regwatch.scheduler.jobs import SchedulerManager
from regwatch.services.settings import SettingsService

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"
_STATIC_DIR = Path(__file__).parent / "web" / "static"


class FirstStartupMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.startswith("/static") or path.startswith("/settings"):
            return await call_next(request)
        if not request.app.state.llm_client.chat_model:
            # HTMX requests must NOT receive the 307 redirect: htmx would
            # follow it, swap the entire /settings/setup page (which itself
            # extends base.html and includes the sidebar) into the small
            # fragment slot that issued the request, and the newly-injected
            # page's own status-bar poll would fire immediately, looping
            # and visually stacking sidebars. Return an empty 200 instead
            # so the swap clears the slot harmlessly.
            if request.headers.get("hx-request", "").lower() == "true":
                return Response(content="", status_code=200)
            return StarletteRedirect(url="/settings/setup")
        return await call_next(request)


def create_app() -> FastAPI:
    config_path = Path(os.environ.get("REGWATCH_CONFIG", "config.yaml"))
    config = load_config(config_path)

    engine = create_app_engine(config.paths.db_file)
    upgrade_schema(engine, embedding_dim=config.llm.embedding_dim)
    seed_defaults(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    # Build the entity-type LLM prompt segment once at startup; the pipeline
    # matcher has no DB session at call time, and per-document DB hits would
    # be wasteful. CRUD routes that mutate the EntityType table must refresh
    # ``app.state.entity_type_prompt`` after writes.
    from regwatch.services.entity_types import prompt_segment
    with session_factory() as session:
        entity_type_prompt = prompt_segment(session)

    from regwatch.analysis.startup import sweep_stuck_runs
    with session_factory() as session:
        sweep_stuck_runs(session)
        session.commit()

    # Load persisted model settings from DB, falling back to config values.
    with session_factory() as session:
        settings_svc = SettingsService(session)
        chat_model = settings_svc.get("chat_model") or config.llm.chat_model or ""
        embedding_model = settings_svc.get("embedding_model") or config.llm.embedding_model or ""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from apscheduler.schedulers.background import BackgroundScheduler  # noqa: PLC0415

        from regwatch.pipeline.run_helpers import run_pipeline_background  # noqa: PLC0415
        from regwatch.services.cssf_discovery import CssfDiscoveryService  # noqa: PLC0415
        from regwatch.services.discovery_runner import run_catalog_refresh  # noqa: PLC0415
        from regwatch.web.task_guard import try_start  # noqa: PLC0415

        bg_scheduler = BackgroundScheduler(timezone=config.ui.timezone)
        pipeline_progress = PipelineProgress()
        app.state.pipeline_progress = pipeline_progress

        def _scheduled_pipeline() -> None:
            def _mark_running() -> None:
                pipeline_progress.reset_for_run(total_sources=0)
                pipeline_progress.message = "Scheduled pipeline run starting..."

            busy = try_start(app.state, _mark_running)
            if busy is not None:
                logger.info("Scheduled pipeline skipped — %s is running", busy)
                return
            run_pipeline_background(
                session_factory=session_factory,
                config=config,
                llm_client=app.state.llm_client,
                progress=pipeline_progress,
                entity_type_prompt=getattr(
                    app.state, "entity_type_prompt", None
                ),
            )

        def _scheduled_cssf_run(mode: Literal["full", "incremental"]) -> None:
            progress = app.state.cssf_discovery_progress
            busy = try_start(app.state, lambda: progress.start(0))
            if busy is not None:
                logger.info("Scheduled CSSF %s run skipped — %s is running", mode, busy)
                return
            logger.info("Scheduled CSSF discovery (%s) starting", mode)
            status = "FAILED"
            try:
                service = CssfDiscoveryService(
                    session_factory=session_factory,
                    config=config.cssf_discovery,
                    on_progress=lambda **kw: progress.tick(**{
                        k: v for k, v in kw.items()
                        if k in ("total_scraped", "entity_type", "reference")
                    }),
                )
                service.run(
                    entity_types=[a.type for a in config.entity.authorizations],
                    mode=mode,
                    triggered_by="SCHEDULER",
                )
                status = "SUCCESS"
                logger.info("Scheduled CSSF discovery (%s) completed", mode)
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled CSSF discovery (%s) failed", mode)
            finally:
                progress.finish(status)

        def _scheduled_discovery() -> None:
            _scheduled_cssf_run("incremental")

        def _scheduled_reconciliation() -> None:
            _scheduled_cssf_run("full")

        def _scheduled_analysis() -> None:
            progress = app.state.analysis_progress
            busy = try_start(
                app.state, lambda: progress.start(0, 0, task="Catalog refresh")
            )
            if busy is not None:
                logger.info("Scheduled catalog refresh skipped — %s is running", busy)
                return
            logger.info("Scheduled catalog refresh & analysis starting")
            auth_types = [a.type for a in config.entity.authorizations]
            from regwatch.services.runtime_limits import (  # noqa: PLC0415
                get_max_runtime_seconds,
            )
            with session_factory() as session:
                max_runtime = get_max_runtime_seconds(session, config, "analysis")
            run_catalog_refresh(
                session_factory=session_factory,
                llm=app.state.llm_client,
                auth_types=auth_types,
                progress=app.state.analysis_progress,
                max_runtime_seconds=max_runtime,
            )
            logger.info(
                "Scheduled catalog refresh & analysis finished with status %s",
                app.state.analysis_progress.status,
            )

        scheduler_manager = SchedulerManager(
            scheduler=bg_scheduler,
            jobs={
                SchedulerManager.PIPELINE_JOB_ID: _scheduled_pipeline,
                SchedulerManager.DISCOVERY_JOB_ID: _scheduled_discovery,
                SchedulerManager.RECONCILIATION_JOB_ID: _scheduled_reconciliation,
                SchedulerManager.ANALYSIS_JOB_ID: _scheduled_analysis,
            },
        )

        # DB key prefix -> (job_id, default_enabled, default_freq, default_time)
        job_defaults = {
            "scheduler_": (SchedulerManager.PIPELINE_JOB_ID, "true", "2days", "06:00"),
            "discovery_": (SchedulerManager.DISCOVERY_JOB_ID, "true", "weekly", "05:30"),
            "reconciliation_": (
                SchedulerManager.RECONCILIATION_JOB_ID, "true", "weekly", "05:00",
            ),
            "analysis_": (SchedulerManager.ANALYSIS_JOB_ID, "false", "monthly", "04:00"),
        }
        with session_factory() as session:
            svc = SettingsService(session)
            for prefix, (job_id, def_en, def_fr, def_ti) in job_defaults.items():
                enabled = svc.get(f"{prefix}enabled", def_en) or def_en
                freq = svc.get(f"{prefix}frequency", def_fr) or def_fr
                time_str = svc.get(f"{prefix}time", def_ti) or def_ti
                scheduler_manager.apply_schedule(job_id, freq, time_str)
                if enabled != "true":
                    scheduler_manager.pause(job_id)

        # Auto-select/repair the chat model against whatever the LLM server
        # currently serves. Health-gated and network-guarded so it never
        # blocks a real server boot when the LLM is unreachable.
        from regwatch.llm.model_selection import refresh_chat_model  # noqa: PLC0415
        try:
            refresh_chat_model(app.state.llm_client, session_factory)
        except Exception:  # noqa: BLE001
            logger.exception("Chat-model auto-selection failed at startup")

        bg_scheduler.start()
        app.state.scheduler_manager = scheduler_manager
        yield
        if bg_scheduler.running:
            bg_scheduler.shutdown(wait=False)

    app = FastAPI(title="Regulatory Watcher", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Register a Jinja filter to render LLM markdown as HTML. Raw HTML is
    # escaped: the output is marked |safe, and answers quote fetched documents.
    from markdown_it import MarkdownIt
    _md = MarkdownIt("commonmark", {"html": False})

    def _render_markdown(text: str) -> str:
        if not text:
            return ""
        return _md.render(text)

    templates.env.filters["markdown"] = _render_markdown

    app.state.templates = templates
    app.state.config = config
    app.state.session_factory = session_factory
    if config.cssf_discovery.entity_filter_ids:
        logger.warning(
            "config.cssf_discovery.entity_filter_ids is deprecated; "
            "manage filter IDs from Settings → Entity Types. Ignoring %s",
            config.cssf_discovery.entity_filter_ids,
        )
    app.state.entity_type_prompt = entity_type_prompt
    app.state.llm_client = LLMClient(
        base_url=config.llm.base_url,
        chat_model=chat_model,
        embedding_model=embedding_model,
        timeout=float(config.analysis.llm_call_timeout_seconds),
    )
    # Provide a default PipelineProgress so that routes work even when
    # the lifespan has not run yet (e.g. in tests without a context manager).
    # The lifespan will overwrite this with the scheduler-managed instance.
    app.state.pipeline_progress = PipelineProgress()
    from regwatch.analysis.progress import AnalysisProgress
    app.state.analysis_progress = AnalysisProgress()
    from regwatch.discovery.progress import CssfDiscoveryProgress
    app.state.cssf_discovery_progress = CssfDiscoveryProgress()
    app.add_middleware(FirstStartupMiddleware)
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/static", StaticFiles(directory=str(_STATIC_DIR)), name="static"
    )

    from regwatch.web.routes import (
        actions,
        catalog,
        chat,
        dashboard,
        db_admin,
        deadlines,
        drafts,
        ict,
        inbox,
        regulation_detail,
    )
    from regwatch.web.routes import (
        analysis as analysis_routes,
    )
    from regwatch.web.routes import (
        discovery as discovery_routes,
    )
    from regwatch.web.routes import (
        entity_types as entity_types_routes,
    )
    from regwatch.web.routes import (
        schedules as schedules_routes,
    )
    from regwatch.web.routes import (
        settings as settings_routes,
    )

    app.include_router(dashboard.router)
    app.include_router(inbox.router)
    app.include_router(catalog.router)
    app.include_router(regulation_detail.router)
    app.include_router(drafts.router)
    app.include_router(deadlines.router)
    app.include_router(ict.router)
    app.include_router(chat.router)
    app.include_router(settings_routes.router)
    app.include_router(schedules_routes.router)
    app.include_router(actions.router)
    app.include_router(analysis_routes.router)
    app.include_router(discovery_routes.router)
    app.include_router(entity_types_routes.router)
    app.include_router(db_admin.router)

    return app


app = create_app()
