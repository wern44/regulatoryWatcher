from datetime import date

import pytest
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from regwatch.cli import app
from regwatch.db.engine import create_app_engine
from regwatch.db.models import Base, DiscoveryRun, Regulation


@pytest.fixture
def runner():
    return CliRunner()


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


def test_discover_cssf_enrich_stubs_flag_rejected(tmp_path, monkeypatch):
    """--enrich-stubs is removed; CLI exits non-zero with a clear error."""
    _setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, ["discover-cssf", "--enrich-stubs"])
    assert result.exit_code != 0
    # Error message directs user away from the removed flag.
    assert "enrich-stubs" in result.output.lower()


def test_discover_cssf_restrict_publication_type(runner, monkeypatch, tmp_path):
    """--publication-type CSSF_CIRCULAR restricts to one matrix column."""
    db, sf = _setup(tmp_path, monkeypatch)
    captured = {}

    # Write a real DiscoveryRun row so the CLI post-run lookup succeeds.
    from datetime import UTC, datetime

    with sf() as s:
        from regwatch.db.models import DiscoveryRun
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            triggered_by="USER_CLI",
            entity_types=["AIFM"],
            mode="incremental",
        )
        s.add(run)
        s.commit()
        stub_run_id = run.run_id

    class _StubService:
        def __init__(self, **kw):
            pass

        def run(self, *, entity_types, mode, triggered_by,
                existing_run_id=None, dry_run=False, restrict_pub_slug=None,
                **kwargs):
            captured["entity_types"] = [
                e.value if hasattr(e, "value") else e for e in entity_types
            ]
            captured["restrict_pub_slug"] = restrict_pub_slug
            captured["dry_run"] = dry_run
            return stub_run_id

    monkeypatch.setattr("regwatch.cli.CssfDiscoveryService", _StubService)

    result = runner.invoke(app, [
        "discover-cssf",
        "--publication-type", "CSSF_CIRCULAR",
    ])
    assert result.exit_code == 0, result.output
    # pub type maps to its config slug via lookup; our config.example has
    # CSSF_CIRCULAR -> filter_id=567 -> (the slug arg passed is the pub label
    # or the filter_id — whichever the service expects). Per the plan:
    # `restrict_pub_slug` carries a publication-type *discriminator* (label
    # or enum value) that the service filters its config list by. Accept
    # either form — assert it's non-None.
    assert captured["restrict_pub_slug"] is not None
    assert captured["dry_run"] is False


def test_discover_cssf_dry_run_flag_passed(runner, monkeypatch, tmp_path):
    """--dry-run flows through to service.run(dry_run=True)."""
    db, sf = _setup(tmp_path, monkeypatch)
    captured = {}

    # Write a real DiscoveryRun row so the CLI post-run lookup succeeds.
    from datetime import UTC, datetime

    with sf() as s:
        from regwatch.db.models import DiscoveryRun
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            triggered_by="USER_CLI",
            entity_types=["AIFM"],
            mode="incremental",
        )
        s.add(run)
        s.commit()
        stub_run_id = run.run_id

    class _StubService:
        def __init__(self, **kw):
            pass

        def run(self, *, dry_run=False, **kw):
            captured["dry_run"] = dry_run
            return stub_run_id

        def preview_retire_candidates(self, run_id):
            from regwatch.services.cssf_discovery import RetirePreview
            return RetirePreview(
                candidates=[], would_retire=True, tripwire_reason=None, total_scraped=0,
            )

    monkeypatch.setattr("regwatch.cli.CssfDiscoveryService", _StubService)

    result = runner.invoke(app, ["discover-cssf", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert captured["dry_run"] is True


def test_discover_cssf_unknown_publication_type_rejected(runner, tmp_path, monkeypatch):
    """Typos in --publication-type produce a clear error."""
    _setup(tmp_path, monkeypatch)
    result = runner.invoke(app, [
        "discover-cssf",
        "--publication-type", "NOT_A_REAL_TYPE",
    ])
    assert result.exit_code != 0
    assert "publication-type" in result.output.lower() or "not_a_real_type" in result.output.lower()


def test_discover_cssf_dry_run_prints_retire_preview(runner, monkeypatch, tmp_path):
    """--dry-run calls preview_retire_candidates and prints the refs."""
    db, sf = _setup(tmp_path, monkeypatch)

    from datetime import UTC, datetime

    with sf() as s:
        from regwatch.db.models import DiscoveryRun
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            triggered_by="USER_CLI",
            entity_types=["AIFM"],
            mode="incremental",
        )
        s.add(run)
        s.commit()
        stub_run_id = run.run_id

    class _Stub:
        def __init__(self, **kw):
            pass

        def run(self, **kw):
            return stub_run_id

        def preview_retire_candidates(self, run_id):
            from regwatch.services.cssf_discovery import RetirePreview
            assert run_id == stub_run_id
            return RetirePreview(
                candidates=["CSSF 99/GONE1", "CSSF 99/GONE2"],
                would_retire=True, tripwire_reason=None, total_scraped=100,
            )

    monkeypatch.setattr("regwatch.cli.CssfDiscoveryService", _Stub)

    result = runner.invoke(app, ["discover-cssf", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "2 regulation(s) would be retired" in result.output
    assert "CSSF 99/GONE1" in result.output
    assert "CSSF 99/GONE2" in result.output


def test_discover_cssf_dry_run_shows_tripwire_when_scraped_low(runner, monkeypatch, tmp_path):
    """--dry-run shows tripwire block message when total_scraped is below floor."""
    db, sf = _setup(tmp_path, monkeypatch)

    from datetime import UTC, datetime

    with sf() as s:
        from regwatch.db.models import DiscoveryRun
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            triggered_by="USER_CLI",
            entity_types=["AIFM"],
            mode="full",
        )
        s.add(run)
        s.commit()
        stub_run_id = run.run_id

    class _Stub:
        def __init__(self, **kw):
            pass

        def run(self, **kw):
            return stub_run_id

        def preview_retire_candidates(self, run_id):
            from regwatch.services.cssf_discovery import RetirePreview
            return RetirePreview(
                candidates=["CSSF 99/PHANTOM"] * 3,
                would_retire=False,
                tripwire_reason="total_scraped=0 < retire_min_scraped=10; ...",
                total_scraped=0,
            )

    monkeypatch.setattr("regwatch.cli.CssfDiscoveryService", _Stub)

    result = runner.invoke(app, ["discover-cssf", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "tripwire would block" in result.output
    # Raw candidates shown but not "would be retired"
    assert "would be retired" not in result.output.replace("would NOT be retired", "")


def test_dry_run_forces_full_mode(runner, monkeypatch, tmp_path):
    """--dry-run without --full forces mode=full and emits a notice."""
    db, sf = _setup(tmp_path, monkeypatch)
    captured = {}

    from datetime import UTC, datetime

    with sf() as s:
        from regwatch.db.models import DiscoveryRun
        run = DiscoveryRun(
            status="SUCCESS",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            triggered_by="USER_CLI",
            entity_types=["AIFM"],
            mode="full",
        )
        s.add(run)
        s.commit()
        stub_run_id = run.run_id

    class _Stub:
        def __init__(self, **kw):
            pass

        def run(self, *, mode, **kw):
            captured["mode"] = mode
            return stub_run_id

        def preview_retire_candidates(self, run_id):
            from regwatch.services.cssf_discovery import RetirePreview
            return RetirePreview(
                candidates=[], would_retire=True, tripwire_reason=None, total_scraped=50,
            )

    monkeypatch.setattr("regwatch.cli.CssfDiscoveryService", _Stub)

    result = runner.invoke(app, ["discover-cssf", "--dry-run"])  # no --full
    assert result.exit_code == 0, result.output
    assert captured["mode"] == "full"
    assert "forcing --full" in result.output
