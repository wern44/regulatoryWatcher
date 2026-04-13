from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfWriter
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.llm.client import HealthStatus
from tests.integration.test_app_smoke import _client


def _make_unprotected_pdf(path: Path, text: str) -> None:
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, text)
    c.save()


def _seed_protected_version(db_file: Path) -> int:
    engine = create_app_engine(db_file)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        reg = Regulation(
            type=RegulationType.CSSF_CIRCULAR,
            reference_number="CSSF 18/698",
            title="IFM",
            issuing_authority="CSSF",
            lifecycle_stage=LifecycleStage.IN_FORCE,
            is_ict=False,
            source_of_truth="SEED",
            url="https://example.com",
        )
        session.add(reg)
        session.flush()

        v = DocumentVersion(
            regulation_id=reg.regulation_id,
            version_number=1,
            is_current=True,
            fetched_at=datetime.now(timezone.utc),
            source_url="https://example.com/locked.pdf",
            content_hash="1" * 64,
            html_text=None,
            pdf_is_protected=True,
            pdf_manual_upload=False,
        )
        session.add(v)
        session.commit()
        return v.version_id


def _patch_llm_health(client) -> None:
    client.app.state.llm_client.health = lambda: HealthStatus(  # type: ignore[assignment]
        reachable=True,
        chat_model_available=True,
        embedding_model_available=True,
    )


def test_settings_view_lists_protected_versions(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _seed_protected_version(tmp_path / "app.db")
    _patch_llm_health(client)

    r = client.get("/settings")
    assert r.status_code == 200
    assert "CSSF 18/698" not in r.text  # settings shows by ID only
    assert "version 1" in r.text


def test_upload_pdf_clears_protection_flag(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    vid = _seed_protected_version(tmp_path / "app.db")
    _patch_llm_health(client)

    pdf_file = tmp_path / "fixed.pdf"
    _make_unprotected_pdf(pdf_file, "Clean extracted text")

    with open(pdf_file, "rb") as f:
        r = client.post(
            f"/settings/upload-pdf/{vid}",
            files={"file": ("fixed.pdf", f, "application/pdf")},
            follow_redirects=False,
        )
    assert r.status_code == 303

    engine = create_app_engine(tmp_path / "app.db")
    with Session(engine) as session:
        v = session.get(DocumentVersion, vid)
        assert v is not None
        assert v.pdf_is_protected is False
        assert v.pdf_manual_upload is True
        assert v.pdf_extracted_text is not None
        assert "Clean extracted text" in v.pdf_extracted_text


# Silence pypdf unused import.
_ = PdfWriter
