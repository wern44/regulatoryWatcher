"""Typer-based CLI for the Regulatory Watcher."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from regwatch.config import AppConfig, load_config
from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentChunk,
    DocumentVersion,
    PipelineRun,
    Regulation,
)
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


@app.command("db-export")
def db_export(
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Where to write the backup file")
    ],
) -> None:
    """Write a consistent online backup of the database to a file."""
    cfg = _get_config()
    from regwatch.db.admin import backup_database  # noqa: PLC0415

    dest = backup_database(cfg.paths.db_file, output)
    typer.echo(f"Backup written to {dest}")


@app.command("db-import")
def db_import(
    file: Annotated[
        Path, typer.Argument(help="Path to the .db backup to restore")
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y", help="Skip the confirmation prompt"
        ),
    ] = False,
) -> None:
    """Replace the current database with `file`. Destructive."""
    cfg = _get_config()
    from regwatch.db.admin import restore_database  # noqa: PLC0415

    if not yes:
        typer.confirm(
            f"This will overwrite {cfg.paths.db_file} with {file}. Continue?",
            abort=True,
        )

    engine = create_app_engine(cfg.paths.db_file)
    restore_database(engine, uploaded_file=file, db_path=Path(cfg.paths.db_file))
    typer.echo(f"Database restored from {file}")


@app.command("db-reset")
def db_reset(
    seed: Annotated[
        bool,
        typer.Option(
            "--seed/--no-seed",
            help="Re-load seeds/regulations_seed.yaml after the reset",
        ),
    ] = True,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y", help="Skip the confirmation prompt"
        ),
    ] = False,
) -> None:
    """Drop every table and recreate the schema. Destructive."""
    cfg = _get_config()
    from regwatch.db.admin import reset_database  # noqa: PLC0415

    if not yes:
        typer.confirm(
            f"This will DROP every table in {cfg.paths.db_file}. Continue?",
            abort=True,
        )

    engine = create_app_engine(cfg.paths.db_file)
    seed_path = Path("seeds/regulations_seed.yaml") if seed else None
    reset_database(
        engine,
        embedding_dim=cfg.ollama.embedding_dim,
        seed_file=seed_path,
    )
    typer.echo(
        "Database reset complete." + (" Seed reloaded." if seed else "")
    )


@app.command("run-pipeline")
def run_pipeline(
    source: Annotated[
        str | None, typer.Option("--source", "-s", help="Only run this source")
    ] = None,
) -> None:
    """Fetch, extract, match, persist — one pass across enabled sources."""
    cfg = _get_config()
    from regwatch.ollama.client import OllamaClient
    from regwatch.pipeline.pipeline_factory import build_runner
    from regwatch.pipeline.sources import build_enabled_sources

    source_instances = build_enabled_sources(cfg, only=source)

    ollama = OllamaClient(
        base_url=cfg.ollama.base_url,
        chat_model=cfg.ollama.chat_model,
        embedding_model=cfg.ollama.embedding_model,
    )

    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as session:
        runner = build_runner(
            session,
            sources=source_instances,
            archive_root=cfg.paths.pdf_archive,
            ollama_client=ollama,
        )
        run_id = runner.run_once()
        session.commit()
    typer.echo(f"Pipeline run {run_id} completed.")


@app.command("reindex")
def reindex() -> None:
    """Clear all chunks and re-embed every current document version."""
    cfg = _get_config()
    from regwatch.ollama.client import OllamaClient
    from regwatch.rag.indexing import index_version

    ollama = OllamaClient(
        base_url=cfg.ollama.base_url,
        chat_model=cfg.ollama.chat_model,
        embedding_model=cfg.ollama.embedding_model,
    )

    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as session:
        # Drop all chunks (cascade removes vec/fts rows via triggers).
        session.query(DocumentChunk).delete()
        session.execute(sa_text("DELETE FROM document_chunk_vec"))
        session.execute(sa_text("DELETE FROM document_chunk_fts"))
        session.flush()

        current = (
            session.query(DocumentVersion)
            .filter(DocumentVersion.is_current.is_(True))
            .all()
        )
        total = 0
        for v in current:
            n = index_version(
                session,
                v,
                ollama=ollama,
                chunk_size_tokens=cfg.rag.chunk_size_tokens,
                overlap_tokens=cfg.rag.chunk_overlap_tokens,
                authorization_types=[a.type for a in cfg.entity.authorizations],
            )
            total += n
        session.commit()
    typer.echo(f"Reindexed {len(current)} version(s), {total} chunk(s).")


@app.command("chat")
def chat(
    question: Annotated[str, typer.Argument(help="Your question")],
) -> None:
    """One-shot RAG: retrieve and answer a single question."""
    cfg = _get_config()
    from regwatch.ollama.client import OllamaClient
    from regwatch.rag.answer import AnswerRequest, generate_answer
    from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters

    ollama = OllamaClient(
        base_url=cfg.ollama.base_url,
        chat_model=cfg.ollama.chat_model,
        embedding_model=cfg.ollama.embedding_model,
    )

    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as session:
        retriever = HybridRetriever(
            session, ollama=ollama, top_k=cfg.rag.retrieval_k
        )
        chunks = retriever.retrieve(question, RetrievalFilters())
        result = generate_answer(
            ollama, AnswerRequest(question=question, chunks=chunks)
        )
    typer.echo(result.answer)
    if result.cited_chunk_ids:
        typer.echo(f"\nCited chunks: {result.cited_chunk_ids}")


@app.command("dump-pipeline-runs")
def dump_pipeline_runs(
    tail: Annotated[
        int, typer.Option("--tail", "-n", help="Number of recent runs")
    ] = 10,
) -> None:
    """Print the N most recent pipeline runs."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as session:
        rows = (
            session.query(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .limit(tail)
            .all()
        )
    if not rows:
        typer.echo("No pipeline runs recorded.")
        return
    header = f"{'run_id':>6} {'status':<10} {'events':>6} {'versions':>8}  started_at"
    typer.echo(header)
    typer.echo("-" * len(header))
    for r in rows:
        typer.echo(
            f"{r.run_id:>6} {r.status:<10} {r.events_created:>6} "
            f"{r.versions_created:>8}  {r.started_at}"
        )
