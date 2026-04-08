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


def _import_sources() -> None:
    """Side-effect imports to populate the source REGISTRY."""
    import regwatch.pipeline.fetch.cssf_consultation  # noqa: F401
    import regwatch.pipeline.fetch.cssf_rss  # noqa: F401
    import regwatch.pipeline.fetch.eba_rss  # noqa: F401
    import regwatch.pipeline.fetch.ec_fisma_rss  # noqa: F401
    import regwatch.pipeline.fetch.esma_rss  # noqa: F401
    import regwatch.pipeline.fetch.eur_lex_adopted  # noqa: F401
    import regwatch.pipeline.fetch.eur_lex_proposal  # noqa: F401
    import regwatch.pipeline.fetch.legilux_parliamentary  # noqa: F401
    import regwatch.pipeline.fetch.legilux_sparql  # noqa: F401


def _instantiate_source(name: str, source_cfg):  # type: ignore[no-untyped-def]
    from regwatch.pipeline.fetch.base import REGISTRY

    cls = REGISTRY[name]
    if name == "cssf_rss":
        return cls(keywords=source_cfg.keywords)
    if name == "eur_lex_adopted":
        return cls(celex_prefixes=source_cfg.celex_prefixes)
    if name == "ec_fisma_rss":
        return cls(
            item_types=source_cfg.item_types, topic_ids=source_cfg.topic_ids
        )
    return cls()


@app.command("run-pipeline")
def run_pipeline(
    source: Annotated[
        str | None, typer.Option("--source", "-s", help="Only run this source")
    ] = None,
) -> None:
    """Fetch, extract, match, persist — one pass across enabled sources."""
    cfg = _get_config()
    _import_sources()
    from regwatch.ollama.client import OllamaClient
    from regwatch.pipeline.pipeline_factory import build_runner

    source_instances = []
    for name, source_cfg in cfg.sources.items():
        if not source_cfg.enabled:
            continue
        if source is not None and name != source:
            continue
        source_instances.append(_instantiate_source(name, source_cfg))

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
