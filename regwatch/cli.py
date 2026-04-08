"""Typer-based CLI for the Regulatory Watcher."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from regwatch.config import AppConfig, load_config
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, Regulation
from regwatch.db.seed import load_seed
from regwatch.db.virtual_tables import create_virtual_tables

app = typer.Typer(help="Regulatory Watcher CLI.")


class _State:
    config: AppConfig | None = None


_state = _State()


@app.callback()
def main(
    config: Annotated[
        Path, typer.Option("--config", "-c", help="Path to config.yaml")
    ] = Path("config.yaml"),
) -> None:
    """Load the configuration for the invoked command."""
    _state.config = load_config(config)


def _get_config() -> AppConfig:
    if _state.config is None:
        raise RuntimeError("Config not loaded")
    return _state.config


@app.command("init-db")
def init_db() -> None:
    """Create the database schema and virtual tables."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=cfg.ollama.embedding_dim)
    typer.echo(f"Schema created in {cfg.paths.db_file}")


@app.command("seed")
def seed(
    file: Annotated[
        Path, typer.Option("--file", "-f", help="Path to the seed YAML")
    ] = Path("seeds/regulations_seed.yaml"),
) -> None:
    """Load the curated seed catalog into the database."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as session:
        load_seed(session, file)
        session.commit()
        count = session.query(Regulation).count()
    typer.echo(f"Loaded seed. {count} regulation(s) in the catalog.")
