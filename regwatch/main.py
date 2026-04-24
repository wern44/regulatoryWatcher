"""FastAPI application factory."""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse as StarletteRedirect

from regwatch.config import load_config
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base
from regwatch.db.schema_sync import sync_schema
from regwatch.db.virtual_tables import create_virtual_tables
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
            return StarletteRedirect(url="/settings/setup")
        return await call_next(request)


def create_app() -> FastAPI:
    config_path = Path(os.environ.get("REGWATCH_CONFIG", "config.yaml"))
    config = load_config(config_path)

    engine = create_app_engine(config.paths.db_file)
    Base.metadata.create_all(engine)
    sync_schema(engine, Base.metadata)
    create_virtual_tables(engine, embedding_dim=config.llm.embedding_dim)
    from regwatch.db.migrations import migrate_discovery_run_item_columns
    migrate_discovery_run_item_columns(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    from regwatch.db.extraction_field_seed import seed_core_fields
    with session_factory() as session:
        seed_core_fields(session)
        session.commit()

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

        from regwatch.db.models import AuthorizationType  # noqa: PLC0415
        from regwatch.pipeline.run_helpers import run_pipeline_background  # noqa: PLC0415
        from regwatch.services.cssf_discovery import CssfDiscoveryService  # noqa: PLC0415
        from regwatch.services.discovery import DiscoveryService  # noqa: PLC0415

        bg_scheduler = BackgroundScheduler(timezone=config.ui.timezone)
        pipeline_progress = PipelineProgress()

        def _any_process_running() -> bool:
            if pipeline_progress.snapshot()["status"] == "running":
                return True
            dp = getattr(app.state, "cssf_discovery_progress", None)
            if dp and getattr(dp, "status", "idle") == "running":
                return True
            return False

        def _scheduled_pipeline() -> None:
            if _any_process_running():
                logger.info("Scheduled pipeline skipped — another process running")
                return
            from datetime import UTC  # noqa: PLC0415
            from datetime import datetime as dt
            pipeline_progress.reset_for_run(total_sources=0)
            pipeline_progress.message = "Scheduled pipeline run starting..."
            pipeline_progress.started_at = dt.now(UTC)
            run_pipeline_background(
                session_factory=session_factory,
                config=config,
                llm_client=app.state.llm_client,
                progress=pipeline_progress,
            )

        def _scheduled_discovery() -> None:
            if _any_process_running():
                logger.info("Scheduled discovery skipped — another process running")
                return
            logger.info("Scheduled CSSF discovery (incremental) starting")
            try:
                auth_types = [
                    AuthorizationType(a.type)
                    for a in config.entity.authorizations
                ]
                service = CssfDiscoveryService(
                    session_factory=session_factory,
                    config=config.cssf_discovery,
                )
                service.run(
                    entity_types=auth_types,
                    mode="incremental",
                    triggered_by="SCHEDULER",
                )
                logger.info("Scheduled CSSF discovery completed")
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled CSSF discovery failed")

        def _scheduled_reconciliation() -> None:
            if _any_process_running():
                logger.info(
                    "Scheduled reconciliation skipped — another process running"
                )
                return
            logger.info("Scheduled CSSF reconciliation (full) starting")
            try:
                auth_types = [
                    AuthorizationType(a.type)
                    for a in config.entity.authorizations
                ]
                service = CssfDiscoveryService(
                    session_factory=session_factory,
                    config=config.cssf_discovery,
                )
                service.run(
                    entity_types=auth_types,
                    mode="full",
                    triggered_by="SCHEDULER",
                )
                logger.info("Scheduled CSSF reconciliation completed")
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled CSSF reconciliation failed")

        def _scheduled_analysis() -> None:
            if _any_process_running():
                logger.info("Scheduled analysis skipped — another process running")
                return
            logger.info("Scheduled catalog refresh & analysis starting")
            try:
                auth_types = [a.type for a in config.entity.authorizations]
                with session_factory() as s:
                    svc = DiscoveryService(s, llm=app.state.llm_client)
                    svc.classify_catalog()
                    svc.discover_missing(auth_types)
                    s.commit()
                logger.info("Scheduled catalog refresh & analysis completed")
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled catalog refresh & analysis failed")

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

        bg_scheduler.start()
        app.state.scheduler_manager = scheduler_manager
        app.state.pipeline_progress = pipeline_progress
        yield
        if bg_scheduler.running:
            bg_scheduler.shutdown(wait=False)

    app = FastAPI(title="Regulatory Watcher", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Register a Jinja filter to render LLM markdown as HTML.
    from markdown_it import MarkdownIt
    _md = MarkdownIt()

    def _render_markdown(text: str) -> str:
        if not text:
            return ""
        return _md.render(text)

    templates.env.filters["markdown"] = _render_markdown

    app.state.templates = templates
    app.state.config = config
    app.state.session_factory = session_factory
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
    app.include_router(db_admin.router)

    return app


app = create_app()
