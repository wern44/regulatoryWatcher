from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from regwatch.cli import app
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, PipelineRun, UpdateEvent
from regwatch.domain.types import RawDocument
from regwatch.pipeline.fetch.base import REGISTRY

runner = CliRunner()


def _minimal_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.yaml"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "pdfs").mkdir()
    (data_dir / "uploads").mkdir()
    config_file.write_text(
        dedent(
            f"""
            entity:
              lei: "TEST1234567890123456"
              legal_name: "Test"
              authorizations:
                - type: AIFM
                  cssf_entity_id: "1"
            sources:
              cssf_rss:
                enabled: true
                interval_hours: 6
                keywords: [aif]
            ollama:
              base_url: "http://localhost:11434"
              chat_model: "llama3.1:8b"
              embedding_model: "nomic-embed-text"
              embedding_dim: 4
            rag:
              chunk_size_tokens: 200
              chunk_overlap_tokens: 20
              retrieval_k: 10
              rerank_k: 5
              enable_rerank: false
            paths:
              db_file: "{(data_dir / 'app.db').as_posix()}"
              pdf_archive: "{(data_dir / 'pdfs').as_posix()}"
              uploads_dir: "{(data_dir / 'uploads').as_posix()}"
            ui:
              language: en
              timezone: "Europe/Luxembourg"
              host: "127.0.0.1"
              port: 8000
            """
        )
    )
    return config_file


class _FakeCssfRssSource:
    """Replacement for the real CSSF RSS source — yields fixed documents."""

    name = "cssf_rss"

    def __init__(self, keywords: list[str]) -> None:
        self.keywords = keywords

    def fetch(self, since: datetime) -> Iterator[RawDocument]:
        now = datetime.now(timezone.utc)
        yield RawDocument(
            source="cssf_rss",
            source_url="https://example.com/fake",
            title="Fake CSSF update",
            published_at=now,
            raw_payload={
                "html_text": "Some content mentioning compliance."
            },
            fetched_at=now,
        )


def test_run_pipeline_command_produces_events(
    tmp_path: Path, monkeypatch
) -> None:
    config_file = _minimal_config(tmp_path)

    # Substitute the registered CssfRssSource with our fake before the command
    # runs. The CLI imports and registers the real class, then looks it up by
    # name — we patch at lookup time.
    import regwatch.pipeline.fetch.cssf_rss  # noqa: F401 — ensures register

    monkeypatch.setitem(REGISTRY, "cssf_rss", _FakeCssfRssSource)

    # Replace the real OllamaClient with a mock so the pipeline's combined
    # matcher falls through to an empty reference list instead of hitting
    # localhost:11434.
    from unittest.mock import MagicMock

    import regwatch.ollama.client as ollama_module

    fake_client_cls = MagicMock()
    fake_instance = MagicMock()
    fake_instance.chat.return_value = "[]"
    fake_instance.embed.return_value = [0.0, 0.0, 0.0, 0.0]
    fake_client_cls.return_value = fake_instance
    monkeypatch.setattr(ollama_module, "OllamaClient", fake_client_cls)

    # init-db first
    result = runner.invoke(app, ["--config", str(config_file), "init-db"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["--config", str(config_file), "run-pipeline"])
    assert result.exit_code == 0, result.output
    assert "Pipeline run" in result.output

    engine = create_app_engine(tmp_path / "data" / "app.db")
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        events = session.query(UpdateEvent).all()
        assert len(events) == 1
        runs = session.query(PipelineRun).all()
        assert len(runs) == 1
        assert runs[0].status == "COMPLETED"


def test_dump_pipeline_runs_command(tmp_path: Path) -> None:
    config_file = _minimal_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config_file), "init-db"])
    assert result.exit_code == 0

    # No runs yet
    result = runner.invoke(
        app, ["--config", str(config_file), "dump-pipeline-runs"]
    )
    assert result.exit_code == 0
    assert "No pipeline runs recorded" in result.output

    # Insert a run directly.
    engine = create_app_engine(tmp_path / "data" / "app.db")
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        session.add(
            PipelineRun(
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                status="COMPLETED",
                sources_attempted=["cssf_rss"],
                sources_failed=[],
                events_created=3,
                versions_created=2,
            )
        )
        session.commit()

    result = runner.invoke(
        app, ["--config", str(config_file), "dump-pipeline-runs"]
    )
    assert result.exit_code == 0
    assert "COMPLETED" in result.output
    assert "3" in result.output
