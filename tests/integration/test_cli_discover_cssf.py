from datetime import date

from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from regwatch.cli import app
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, DiscoveryRun, Regulation


def _setup(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    engine = create_app_engine(db)
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        open("config.example.yaml").read().replace(
            '"./data/app.db"', f'"{db.as_posix()}"'
        )
    )
    monkeypatch.setenv("REGWATCH_CONFIG", str(cfg))
    return db, sf


def _mock_scraper():
    """Monkeypatch returns a single CSSF 22/806 row with a 25/883 amendment."""
    from regwatch.discovery.cssf_scraper import CircularDetail, CircularListingRow

    def _fake_list(
        *, entity_filter_id, content_type_filter_id, publication_type_label, **kwargs
    ):
        yield CircularListingRow(
            reference_number="CSSF 22/806",
            raw_title="Circular CSSF 22/806 on outsourcing",
            description="Outsourcing arrangements.",
            publication_date=date(2022, 4, 22),
            detail_url="https://www.cssf.lu/en/Document/circular-cssf-22-806/",
        )

    def _fake_detail(url, *, client=None, **kwargs):
        return CircularDetail(
            reference_number="CSSF 22/806",
            clean_title="on outsourcing arrangements",
            amended_by_refs=["CSSF 25/883"],
            amends_refs=[],
            supersedes_refs=[],
            applicable_entities=["Alternative investment fund managers"],
            pdf_url_en="https://example.test/cssf22_806eng.pdf",
            pdf_url_fr=None,
            published_at=date(2022, 4, 22),
            updated_at=None,
            description="Outsourcing arrangements.",
        )

    return _fake_list, _fake_detail


def test_cli_discover_cssf_full(tmp_path, monkeypatch):
    _, sf = _setup(tmp_path, monkeypatch)
    fake_list, fake_detail = _mock_scraper()
    monkeypatch.setattr("regwatch.services.cssf_discovery.list_circulars", fake_list)
    monkeypatch.setattr("regwatch.services.cssf_discovery.fetch_circular_detail", fake_detail)

    result = CliRunner().invoke(app, ["discover-cssf", "--full", "--entity", "AIFM"])
    assert result.exit_code == 0, result.output
    assert "Discovery run" in result.output
    assert "SUCCESS" in result.output

    with sf() as s:
        run = s.query(DiscoveryRun).one()
        assert run.status == "SUCCESS"
        assert run.new_count == 1
        assert run.mode == "full"
        assert run.triggered_by == "USER_CLI"
        reg = s.query(Regulation).filter_by(reference_number="CSSF 22/806").one()
        assert reg.source_of_truth == "CSSF_WEB"


def test_cli_discover_cssf_default_incremental(tmp_path, monkeypatch):
    _, sf = _setup(tmp_path, monkeypatch)
    fake_list, fake_detail = _mock_scraper()
    monkeypatch.setattr("regwatch.services.cssf_discovery.list_circulars", fake_list)
    monkeypatch.setattr("regwatch.services.cssf_discovery.fetch_circular_detail", fake_detail)

    result = CliRunner().invoke(app, ["discover-cssf", "--entity", "AIFM"])
    assert result.exit_code == 0, result.output
    with sf() as s:
        run = s.query(DiscoveryRun).one()
        assert run.mode == "incremental"


def test_cli_discover_cssf_uses_configured_entities_when_none_given(tmp_path, monkeypatch):
    _, sf = _setup(tmp_path, monkeypatch)
    fake_list, fake_detail = _mock_scraper()
    monkeypatch.setattr("regwatch.services.cssf_discovery.list_circulars", fake_list)
    monkeypatch.setattr("regwatch.services.cssf_discovery.fetch_circular_detail", fake_detail)

    # No --entity flag -> falls back to cfg.entity.authorizations
    result = CliRunner().invoke(app, ["discover-cssf", "--full"])
    assert result.exit_code == 0, result.output
    with sf() as s:
        run = s.query(DiscoveryRun).one()
        # config.example.yaml has AIFM + CHAPTER15_MANCO configured
        assert set(run.entity_types) == {"AIFM", "CHAPTER15_MANCO"}


def test_cli_discover_cssf_invalid_entity(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["discover-cssf", "--entity", "NONSENSE"])
    assert result.exit_code == 2
    assert "Unknown entity" in result.output


def test_cli_discover_cssf_exits_1_on_failure(tmp_path, monkeypatch):
    _, sf = _setup(tmp_path, monkeypatch)

    # Scraper raises on list -> entire slug fails; run status becomes FAILED
    def _raising_list(
        *, entity_filter_id, content_type_filter_id, publication_type_label, **kwargs
    ):
        raise RuntimeError("mocked scraper failure")
        yield  # unreachable; makes this a generator

    monkeypatch.setattr("regwatch.services.cssf_discovery.list_circulars", _raising_list)
    result = CliRunner().invoke(app, ["discover-cssf", "--full", "--entity", "AIFM"])
    assert result.exit_code == 1, result.output
    assert "FAILED" in result.output
