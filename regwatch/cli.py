"""Typer-based CLI for the Regulatory Watcher."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session, sessionmaker

from regwatch.analysis.runner import AnalysisRunner
from regwatch.analysis.startup import sweep_stuck_runs
from regwatch.config import AppConfig, load_config
from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentChunk,
    DocumentVersion,
    PipelineRun,
    Regulation,
)
from regwatch.db.schema_sync import sync_schema
from regwatch.db.seed import load_seed
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.llm.client import LLMClient
from regwatch.services.analysis import AnalysisService
from regwatch.services.upload import (
    UploadRejectedError,
    index_uploaded_version,
    save_upload,
)

app = typer.Typer(help="Regulatory Watcher CLI.")


class _State:
    config: AppConfig | None = None


_state = _State()


@app.callback()
def main(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Path to config.yaml")
    ] = None,
) -> None:
    """Load the configuration for the invoked command."""
    if config is None:
        config = Path(os.environ.get("REGWATCH_CONFIG", "config.yaml"))
    _state.config = load_config(config)


def _get_config() -> AppConfig:
    if _state.config is None:
        raise RuntimeError("Config not loaded")
    return _state.config


def _build_llm(cfg: AppConfig) -> LLMClient:
    from regwatch.services.settings import SettingsService
    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as s:
        svc = SettingsService(s)
        chat_model = svc.get("chat_model") or cfg.llm.chat_model or ""
        embedding_model = svc.get("embedding_model") or cfg.llm.embedding_model or ""
    return LLMClient(
        base_url=cfg.llm.base_url,
        chat_model=chat_model,
        embedding_model=embedding_model,
    )


@app.command("init-db")
def init_db() -> None:
    """Create the database schema and virtual tables."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    Base.metadata.create_all(engine)
    sync_schema(engine, Base.metadata)
    create_virtual_tables(engine, embedding_dim=cfg.llm.embedding_dim)
    from regwatch.db.extraction_field_seed import seed_core_fields
    with Session(engine) as session:
        seed_core_fields(session)
        session.commit()
    with Session(engine) as session:
        sweep_stuck_runs(session)
        session.commit()
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
        embedding_dim=cfg.llm.embedding_dim,
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
    from regwatch.llm.client import LLMClient
    from regwatch.pipeline.pipeline_factory import build_runner
    from regwatch.pipeline.sources import build_enabled_sources

    source_instances = build_enabled_sources(cfg, only=source)

    llm = LLMClient(
        base_url=cfg.llm.base_url,
        chat_model=cfg.llm.chat_model or "",
        embedding_model=cfg.llm.embedding_model or "",
    )

    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as session:
        runner = build_runner(
            session,
            sources=source_instances,
            archive_root=cfg.paths.pdf_archive,
            llm_client=llm,
        )
        run_id = runner.run_once()
        session.commit()
    typer.echo(f"Pipeline run {run_id} completed.")


@app.command("reindex")
def reindex() -> None:
    """Clear all chunks and re-embed every current document version."""
    cfg = _get_config()
    from regwatch.llm.client import LLMClient
    from regwatch.rag.indexing import index_version

    llm = LLMClient(
        base_url=cfg.llm.base_url,
        chat_model=cfg.llm.chat_model or "",
        embedding_model=cfg.llm.embedding_model or "",
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
                ollama=llm,
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
    from regwatch.llm.client import LLMClient
    from regwatch.rag.answer import AnswerRequest, generate_answer
    from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters

    llm = LLMClient(
        base_url=cfg.llm.base_url,
        chat_model=cfg.llm.chat_model or "",
        embedding_model=cfg.llm.embedding_model or "",
    )

    engine = create_app_engine(cfg.paths.db_file)
    with Session(engine) as session:
        retriever = HybridRetriever(
            session, ollama=llm, top_k=cfg.rag.retrieval_k
        )
        chunks = retriever.retrieve(question, RetrievalFilters())
        result = generate_answer(
            llm, AnswerRequest(question=question, chunks=chunks)
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


@app.command("analyse")
def analyse(
    reg: Annotated[
        list[str] | None, typer.Option("--reg", help="Regulation reference (repeatable)")
    ] = None,
    all_ict: Annotated[
        bool, typer.Option("--all-ict", help="Analyse every ICT regulation")
    ] = False,
) -> None:
    """Run analysis against selected regulations' current versions."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    sf = sessionmaker(engine, expire_on_commit=False)

    with sf() as s:
        q = s.query(Regulation)
        if all_ict:
            q = q.filter(Regulation.is_ict == True)  # noqa: E712
        elif reg:
            q = q.filter(Regulation.reference_number.in_(reg))
        else:
            typer.echo("Specify --reg REF (repeatable) or --all-ict")
            raise typer.Exit(code=2)
        regs = q.all()
        if not regs:
            typer.echo("No matching regulations.")
            raise typer.Exit(code=1)
        version_ids: list[int] = []
        for r in regs:
            v = next((v for v in r.versions if v.is_current), None)
            if v is not None:
                version_ids.append(v.version_id)
            else:
                typer.echo(f"[!] {r.reference_number} has no current version; skipping")
        if not version_ids:
            typer.echo("Nothing to analyse.")
            raise typer.Exit(code=1)

    llm = _build_llm(cfg)
    runner = AnalysisRunner(
        session_factory=sf, llm=llm, max_document_tokens=cfg.analysis.max_document_tokens,
    )
    run_id = runner.queue_and_run(
        version_ids, triggered_by="USER_CLI", llm_model=llm.chat_model,
    )

    with sf() as s:
        run = AnalysisService(s).get_run(run_id)
    typer.echo(f"Run {run_id}: {run.status}")
    for a in run.analyses:
        mark = "[OK]" if a.status == "SUCCESS" else "[X]"
        typer.echo(f"  {mark} version {a.version_id}: {a.error_detail or 'ok'}")


@app.command("upload")
def upload(
    ref: Annotated[
        str, typer.Option("--reg", help="Regulation reference (e.g. 'CSSF 12/552')")
    ],
    file_path: Annotated[
        Path, typer.Argument(help="Local PDF or HTML file to upload")
    ],
) -> None:
    """Upload a document manually and create a new version for the regulation."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    sf = sessionmaker(engine, expire_on_commit=False)

    if not file_path.exists() or not file_path.is_file():
        typer.echo(f"File not found: {file_path}")
        raise typer.Exit(code=2)

    uploads_dir_str = getattr(cfg.paths, "uploads_dir", None) or cfg.paths.pdf_archive
    uploads_dir = Path(uploads_dir_str)
    data = file_path.read_bytes()

    with sf() as s:
        reg = s.query(Regulation).filter_by(reference_number=ref).one_or_none()
        if reg is None:
            typer.echo(f"No regulation with reference '{ref}'")
            raise typer.Exit(code=1)
        try:
            result = save_upload(
                session=s, regulation_id=reg.regulation_id,
                filename=file_path.name, data=data,
                uploads_dir=uploads_dir,
                max_size_mb=cfg.analysis.max_upload_size_mb,
            )
        except UploadRejectedError as e:
            typer.echo(f"Upload rejected: {e}")
            raise typer.Exit(code=1) from e
        s.commit()

        if result.created:
            llm = _build_llm(cfg)
            auth_types = [a.type for a in cfg.entity.authorizations]
            chunk_count = index_uploaded_version(
                session=s,
                version_id=result.version_id,
                llm=llm,
                chunk_size_tokens=cfg.rag.chunk_size_tokens,
                overlap_tokens=cfg.rag.chunk_overlap_tokens,
                authorization_types=auth_types,
            )
            s.commit()
            typer.echo(f"Indexed {chunk_count} chunks.")

    status = "new" if result.created else "deduped (same content already exists)"
    typer.echo(f"Uploaded -> version {result.version_id} ({status})")
