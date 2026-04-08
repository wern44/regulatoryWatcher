from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from regwatch.cli import app

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
            sources: {{}}
            ollama:
              base_url: "http://localhost:11434"
              chat_model: "llama3.1:8b"
              embedding_model: "nomic-embed-text"
              embedding_dim: 768
            rag:
              chunk_size_tokens: 500
              chunk_overlap_tokens: 50
              retrieval_k: 20
              rerank_k: 10
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


def test_init_db_creates_schema(tmp_path: Path) -> None:
    config_file = _minimal_config(tmp_path)
    result = runner.invoke(app, ["--config", str(config_file), "init-db"])
    assert result.exit_code == 0, result.output
    db_file = tmp_path / "data" / "app.db"
    assert db_file.exists()


def test_seed_loads_catalog(tmp_path: Path) -> None:
    config_file = _minimal_config(tmp_path)
    seed_file = tmp_path / "seed.yaml"
    seed_file.write_text(
        dedent(
            """
            entity:
              lei: "TEST1234567890123456"
              legal_name: "Test"
            authorizations:
              - type: AIFM
                cssf_entity_id: "1"
            regulations:
              - reference_number: "CSSF 18/698"
                type: CSSF_CIRCULAR
                title: "IFM"
                issuing_authority: "CSSF"
                lifecycle_stage: IN_FORCE
                is_ict: false
                url: "https://example.com"
                applicability: BOTH
                aliases:
                  - { pattern: "CSSF 18/698", kind: EXACT }
            """
        )
    )
    runner.invoke(app, ["--config", str(config_file), "init-db"])
    result = runner.invoke(
        app, ["--config", str(config_file), "seed", "--file", str(seed_file)]
    )
    assert result.exit_code == 0, result.output
    assert "1 regulation" in result.output.lower() or "loaded" in result.output.lower()
