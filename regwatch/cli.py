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
    AuthorizationType,
    Base,
    DiscoveryRun,
    DocumentChunk,
    PipelineRun,
    Regulation,
)
from regwatch.db.schema_sync import sync_schema
from regwatch.db.seed import load_seed
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.llm.client import LLMClient
from regwatch.services.analysis import AnalysisService
from regwatch.services.cssf_discovery import CssfDiscoveryService
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
        timeout=float(cfg.analysis.llm_call_timeout_seconds),
    )


@app.command("init-db")
def init_db() -> None:
    """Create the database schema and virtual tables."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    Base.metadata.create_all(engine)
    sync_schema(engine, Base.metadata)
    create_virtual_tables(engine, embedding_dim=cfg.llm.embedding_dim)
    from regwatch.db.migrations import migrate_discovery_run_item_columns
    migrate_discovery_run_item_columns(engine)
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
        timeout=float(cfg.analysis.llm_call_timeout_seconds),
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
def reindex(
    reg: Annotated[
        list[str] | None, typer.Option("--reg", help="Regulation reference (repeatable)")
    ] = None,
    all_: Annotated[
        bool, typer.Option("--all", help="Reindex every regulation")
    ] = False,
) -> None:
    """Re-chunk and re-embed DocumentVersions with the current chunker."""
    from regwatch.rag.indexing import index_version

    if not reg and not all_:
        typer.echo("Specify --reg REF (repeatable) or --all")
        raise typer.Exit(code=2)

    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    sf = sessionmaker(engine, expire_on_commit=False)
    llm = _build_llm(cfg)

    auth_types = [a.type for a in cfg.entity.authorizations]

    with sf() as s:
        q = s.query(Regulation)
        if not all_ and reg:
            q = q.filter(Regulation.reference_number.in_(reg))
        regs = q.all()
        if not regs:
            typer.echo("No matching regulations.")
            raise typer.Exit(code=1)

        total_versions = 0
        total_chunks = 0
        for r in regs:
            for v in r.versions:
                # Wipe existing chunks + virtual-table rows for this version
                s.execute(
                    sa_text(
                        "DELETE FROM document_chunk_vec WHERE chunk_id IN "
                        "(SELECT chunk_id FROM document_chunk WHERE version_id = :vid)"
                    ),
                    {"vid": v.version_id},
                )
                s.execute(
                    sa_text(
                        "DELETE FROM document_chunk_fts WHERE rowid IN "
                        "(SELECT chunk_id FROM document_chunk WHERE version_id = :vid)"
                    ),
                    {"vid": v.version_id},
                )
                s.query(DocumentChunk).filter_by(version_id=v.version_id).delete()
                s.flush()
                n = index_version(
                    s, v, ollama=llm,
                    chunk_size_tokens=cfg.rag.chunk_size_tokens,
                    overlap_tokens=cfg.rag.chunk_overlap_tokens,
                    authorization_types=auth_types,
                )
                total_versions += 1
                total_chunks += n
        s.commit()

    typer.echo(f"Reindexed {total_versions} version(s), {total_chunks} chunk(s).")


@app.command("discover-cssf")
def discover_cssf(
    full: Annotated[
        bool, typer.Option("--full", help="Force full crawl (default: incremental)")
    ] = False,
    entity: Annotated[
        list[str] | None,
        typer.Option(
            "--entity",
            help="AIFM or CHAPTER15_MANCO (repeatable; default: all configured)",
        ),
    ] = None,
    backfill: Annotated[
        bool,
        typer.Option(
            "--backfill",
            help=(
                "Re-fetch detail pages for all CSSF_WEB rows, update titles "
                "and re-run the ICT heuristic against the subtitle."
            ),
        ),
    ] = False,
    reclassify: Annotated[
        bool,
        typer.Option(
            "--reclassify",
            help=(
                "Re-run ICT heuristic on all CSSF_WEB rows (can flip "
                "True->False); respects RegulationOverride."
            ),
        ),
    ] = False,
    enrich_stubs: Annotated[
        bool,
        typer.Option(
            "--enrich-stubs",
            help=(
                "Fetch detail pages for CSSF_STUB rows and promote them to "
                "CSSF_WEB"
            ),
        ),
    ] = False,
) -> None:
    """Discover CSSF circulars for the configured authorizations."""
    cfg = _get_config()
    engine = create_app_engine(cfg.paths.db_file)
    sf = sessionmaker(engine, expire_on_commit=False)

    if backfill:
        service = CssfDiscoveryService(
            session_factory=sf,
            config=cfg.cssf_discovery,
        )
        counts = service.backfill_titles_and_descriptions(triggered_by="USER_CLI")
        typer.echo(f"Backfill complete: {counts}")
        return

    if reclassify:
        service = CssfDiscoveryService(
            session_factory=sf,
            config=cfg.cssf_discovery,
        )
        counts = service.reclassify_cssf_web_ict()
        typer.echo(f"Reclassification complete: {counts}")
        return

    if enrich_stubs:
        service = CssfDiscoveryService(
            session_factory=sf,
            config=cfg.cssf_discovery,
        )
        counts = service.enrich_stubs()
        typer.echo(f"Stub enrichment complete: {counts}")
        return

    # Resolve entity types
    if entity:
        auth_types: list[AuthorizationType] = []
        for name in entity:
            try:
                auth_types.append(AuthorizationType(name))
            except ValueError as e:
                typer.echo(
                    f"Unknown entity: {name!r}. "
                    f"Valid: {[e.value for e in AuthorizationType]}"
                )
                raise typer.Exit(code=2) from e
    else:
        auth_types = [AuthorizationType(a.type) for a in cfg.entity.authorizations]

    if not auth_types:
        typer.echo("No authorization types configured.")
        raise typer.Exit(code=1)

    mode = "full" if full else "incremental"

    service = CssfDiscoveryService(
        session_factory=sf,
        config=cfg.cssf_discovery,
    )

    typer.echo(
        f"Starting CSSF discovery: mode={mode}, "
        f"entity_types={[e.value for e in auth_types]}"
    )
    run_id = service.run(
        entity_types=auth_types,
        mode=mode,
        triggered_by="USER_CLI",
    )

    with sf() as s:
        run = s.get(DiscoveryRun, run_id)
        if run is None:
            typer.echo(f"Run {run_id} not found after completion.")
            raise typer.Exit(code=1)

        typer.echo(f"Discovery run {run.run_id}: {run.status}")
        typer.echo(f"  total scraped: {run.total_scraped}")
        typer.echo(f"  NEW:          {run.new_count}")
        typer.echo(f"  AMENDED:      {run.amended_count}")
        typer.echo(f"  UPDATED:      {run.updated_count}")
        typer.echo(f"  UNCHANGED:    {run.unchanged_count}")
        typer.echo(f"  WITHDRAWN:    {run.withdrawn_count}")
        typer.echo(f"  FAILED:       {run.failed_count}")
        if run.error_summary:
            typer.echo("Errors:")
            typer.echo(run.error_summary)

        if run.status != "SUCCESS":
            raise typer.Exit(code=1)


@app.command("chat")
def chat(
    question: Annotated[str, typer.Argument(help="Your question")],
    version: Annotated[
        list[int] | None,
        typer.Option(
            "--version",
            help="Limit retrieval to a document_version id (repeatable)",
        ),
    ] = None,
    reg: Annotated[
        list[str] | None,
        typer.Option(
            "--reg",
            help=(
                "Limit retrieval to a regulation reference; expands to its "
                "CURRENT version (repeatable)"
            ),
        ),
    ] = None,
) -> None:
    """One-shot RAG: retrieve and answer a single question."""
    cfg = _get_config()
    from regwatch.rag.chat_service import ChatService
    from regwatch.rag.retrieval import RetrievalFilters

    llm = _build_llm(cfg)

    engine = create_app_engine(cfg.paths.db_file)
    sf = sessionmaker(engine, expire_on_commit=False)

    version_ids: list[int] = list(version or [])
    if reg:
        with sf() as s:
            regs = (
                s.query(Regulation)
                .filter(Regulation.reference_number.in_(reg))
                .all()
            )
            missing = set(reg) - {r.reference_number for r in regs}
            for ref in missing:
                typer.echo(f"[!] no regulation with reference '{ref}'; skipping")
            for r in regs:
                current = next((v for v in r.versions if v.is_current), None)
                if current is not None:
                    version_ids.append(current.version_id)
                else:
                    typer.echo(
                        f"[!] {r.reference_number} has no current version; skipping"
                    )

    filters = RetrievalFilters(version_ids=version_ids)
    with sf() as session:
        svc = ChatService(session, ollama=llm, top_k=cfg.rag.retrieval_k)
        answer = svc.ask_adhoc(question, filters=filters)
    typer.echo(answer)


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
