from datetime import UTC, datetime

from regwatch.db.models import (
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from tests.integration.test_app_smoke import _client


def _seed(c):
    with c.app.state.session_factory() as s:
        for ref in ("CSSF 12/552", "CSSF 20/759"):
            reg = Regulation(
                type=RegulationType.CSSF_CIRCULAR,
                reference_number=ref,
                title=f"T {ref}",
                issuing_authority="CSSF",
                lifecycle_stage=LifecycleStage.IN_FORCE,
                url="x",
                source_of_truth="SEED",
            )
            s.add(reg)
            s.flush()
            v = DocumentVersion(
                regulation_id=reg.regulation_id,
                version_number=1,
                is_current=True,
                fetched_at=datetime.now(UTC),
                source_url="x",
                content_hash=f"h{ref}",
            )
            s.add(v)
            s.flush()
        s.commit()


def test_chat_scope_picker_renders_regulations_and_versions(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _seed(c)
    # The page that contains the ask form (either /chat or a dedicated /chat/ask page)
    # Try both; at least one should render the scope picker.
    for path in ("/chat/ask", "/chat"):
        r = c.get(path)
        if r.status_code == 200:
            t = r.text
            if 'name="version_ids"' in t and "CSSF 12/552" in t:
                # Found the scope picker
                assert "CSSF 20/759" in t
                # The form posts to /chat/ask
                assert 'action="/chat/ask"' in t or 'action="/chat"' in t
                return
    raise AssertionError(
        "Expected either /chat/ask or /chat to render a scope picker "
        "with name='version_ids' checkboxes"
    )
