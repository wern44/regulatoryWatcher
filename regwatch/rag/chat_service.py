"""Chat service: ties together retrieval, answer generation, and persistence."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from regwatch.db.models import ChatMessage, ChatSession
from regwatch.ollama.client import OllamaClient
from regwatch.rag.answer import AnswerRequest, generate_answer
from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters


class ChatService:
    def __init__(
        self, session: Session, ollama: OllamaClient, top_k: int = 10
    ) -> None:
        self._session = session
        self._ollama = ollama
        self._retriever = HybridRetriever(session, ollama=ollama, top_k=top_k)

    def create_session(
        self, title: str, filters: RetrievalFilters
    ) -> ChatSession:
        row = ChatSession(
            title=title,
            created_at=datetime.now(timezone.utc),
            filters=asdict(filters),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def ask(self, session_id: int, question: str) -> ChatMessage:
        cs = self._session.get(ChatSession, session_id)
        if cs is None:
            raise ValueError(f"ChatSession {session_id} not found")
        filters = RetrievalFilters(**cs.filters)

        self._session.add(
            ChatMessage(
                session_id=session_id,
                role="user",
                content=question,
                retrieved_chunk_ids=[],
                created_at=datetime.now(timezone.utc),
            )
        )
        self._session.flush()

        chunks = self._retriever.retrieve(question, filters)
        result = generate_answer(
            self._ollama, AnswerRequest(question=question, chunks=chunks)
        )

        assistant = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=result.answer,
            retrieved_chunk_ids=result.cited_chunk_ids,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(assistant)
        self._session.flush()
        return assistant

    def list_messages(self, session_id: int) -> list[ChatMessage]:
        return (
            self._session.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at, ChatMessage.message_id)
            .all()
        )
