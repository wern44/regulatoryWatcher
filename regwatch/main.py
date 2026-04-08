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

from regwatch.config import load_config
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.ollama.client import OllamaClient
from regwatch.scheduler.jobs import build_scheduler

_TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"
_STATIC_DIR = Path(__file__).parent / "web" / "static"


def create_app() -> FastAPI:
    config_path = Path(os.environ.get("REGWATCH_CONFIG", "config.yaml"))
    config = load_config(config_path)

    engine = create_app_engine(config.paths.db_file)
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=config.ollama.embedding_dim)
    session_factory = sessionmaker(engine, expire_on_commit=False)

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
    app.state.ollama_client = OllamaClient(
        base_url=config.ollama.base_url,
        chat_model=config.ollama.chat_model,
        embedding_model=config.ollama.embedding_model,
    )
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/static", StaticFiles(directory=str(_STATIC_DIR)), name="static"
    )

    from regwatch.web.routes import (
        catalog,
        chat,
        dashboard,
        deadlines,
        drafts,
        ict,
        inbox,
        regulation_detail,
    )

    app.include_router(dashboard.router)
    app.include_router(inbox.router)
    app.include_router(catalog.router)
    app.include_router(regulation_detail.router)
    app.include_router(drafts.router)
    app.include_router(deadlines.router)
    app.include_router(ict.router)
    app.include_router(chat.router)

    return app


app = create_app()
