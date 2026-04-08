from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from regwatch.db.engine import create_app_engine
from regwatch.db.models import (
    Base,
    ChatMessage,
    DocumentVersion,
    LifecycleStage,
    Regulation,
    RegulationType,
)
from regwatch.db.virtual_tables import create_virtual_tables
from regwatch.rag.chat_service import ChatService
from regwatch.rag.indexing import index_version
from regwatch.rag.retrieval import RetrievalFilters


def _session_with_content(tmp_path: Path) -> Session:
    engine = create_app_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    create_virtual_tables(engine, embedding_dim=4)
    session = Session(engine)

    reg = Regulation(
        type=RegulationType.EU_REGULATION,
        reference_number="DORA",
        title="DORA",
        issuing_authority="EU",
        lifecycle_stage=LifecycleStage.IN_FORCE,
        is_ict=True,
        source_of_truth="SEED",
        url="https://example.com",
    )
    session.add(reg)
    session.flush()

    version = DocumentVersion(
        regulation_id=reg.regulation_id,
        version_number=1,
        is_current=True,
        fetched_at=datetime.now(timezone.utc),
        source_url="https://example.com",
        content_hash="z" * 64,
        html_text="DORA Article 24 covers ICT risk management requirements.",
        pdf_is_protected=False,
        pdf_manual_upload=False,
    )
    session.add(version)
    session.flush()

    fake = MagicMock()
    fake.embed.return_value = [1.0, 0.0, 0.0, 0.0]
    index_version(
        session,
        version,
        ollama=fake,
        chunk_size_tokens=200,
        overlap_tokens=20,
        authorization_types=["AIFM"],
    )
    session.commit()
    return session


def test_ask_stores_user_and_assistant_messages(tmp_path: Path) -> None:
    session = _session_with_content(tmp_path)

    ollama = MagicMock()
    ollama.embed.return_value = [1.0, 0.0, 0.0, 0.0]
    ollama.chat.return_value = "DORA Article 24 covers ICT risk (chunk 1)."

    service = ChatService(session, ollama=ollama, top_k=5)
    chat_session = service.create_session(
        title="DORA questions", filters=RetrievalFilters()
    )
    session.flush()

    assistant_msg = service.ask(chat_session.session_id, "What does Article 24 cover?")
    session.commit()

    messages = (
        session.query(ChatMessage)
        .filter(ChatMessage.session_id == chat_session.session_id)
        .order_by(ChatMessage.created_at, ChatMessage.message_id)
        .all()
    )
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "What does Article 24 cover?"
    assert messages[1].message_id == assistant_msg.message_id
    assert "Article 24" in messages[1].content
    assert len(messages[1].retrieved_chunk_ids) >= 1


def test_list_messages_returns_ordered_history(tmp_path: Path) -> None:
    session = _session_with_content(tmp_path)

    ollama = MagicMock()
    ollama.embed.return_value = [1.0, 0.0, 0.0, 0.0]
    ollama.chat.side_effect = ["First reply.", "Second reply."]

    service = ChatService(session, ollama=ollama, top_k=5)
    chat_session = service.create_session(
        title="Multi turn", filters=RetrievalFilters()
    )
    session.flush()

    service.ask(chat_session.session_id, "First question?")
    service.ask(chat_session.session_id, "Second question?")
    session.commit()

    history = service.list_messages(chat_session.session_id)
    assert [m.role for m in history] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert history[0].content == "First question?"
    assert history[1].content == "First reply."
    assert history[2].content == "Second question?"
    assert history[3].content == "Second reply."
