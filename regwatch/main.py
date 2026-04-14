"""FastAPI application factory."""
from __future__ import annotations

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
from regwatch.scheduler.jobs import build_scheduler
from regwatch.services.settings import SettingsService

_TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"
_STATIC_DIR = Path(__file__).parent / "web" / "static"


class FirstStartupMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
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
        scheduler = build_scheduler(
            config,
            run_pipeline_for=lambda sources: None,
            start=False,
        )
        app.state.scheduler = scheduler
        app.state.config = config
        app.state.session_factory = session_factory
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="Regulatory Watcher", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.templates = templates
    app.state.config = config
    app.state.session_factory = session_factory
    app.state.llm_client = LLMClient(
        base_url=config.llm.base_url,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )
    app.state.pipeline_progress = PipelineProgress()
    from regwatch.analysis.progress import AnalysisProgress
    app.state.analysis_progress = AnalysisProgress()
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
    app.include_router(actions.router)
    app.include_router(analysis_routes.router)
    app.include_router(db_admin.router)

    return app


app = create_app()
