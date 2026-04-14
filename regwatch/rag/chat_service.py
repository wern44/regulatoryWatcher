"""Chat service: ties together retrieval, answer generation, and persistence."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from regwatch.db.models import ChatMessage, ChatSession, Regulation
from regwatch.llm.client import LLMClient
from regwatch.rag.answer import AnswerRequest, generate_answer
from regwatch.rag.retrieval import HybridRetriever, RetrievalFilters, RetrievedChunk


def _has_active_scope(f: RetrievalFilters) -> bool:
    return bool(
        f.version_ids
        or f.regulation_ids
        or f.authorization_type
        or f.lifecycle_stages
        or f.is_ict is not None
    )


class ChatService:
    def __init__(
        self, session: Session, ollama: LLMClient, top_k: int = 10
    ) -> None:
        self._session = session
        self._ollama = ollama
        self._retriever = HybridRetriever(session, ollama=ollama, top_k=top_k)

    def create_session(
        self, title: str, filters: RetrievalFilters
    ) -> ChatSession:
        row = ChatSession(
            title=title,
            created_at=datetime.now(UTC),
            filters=asdict(filters),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def ask(
        self,
        session_id: int,
        question: str,
        *,
        filters: RetrievalFilters | None = None,
    ) -> ChatMessage:
        cs = self._session.get(ChatSession, session_id)
        if cs is None:
            raise ValueError(f"ChatSession {session_id} not found")
        if filters is None:
            filters = RetrievalFilters(**cs.filters)

        self._session.add(
            ChatMessage(
                session_id=session_id,
                role="user",
                content=question,
                retrieved_chunk_ids=[],
                created_at=datetime.now(UTC),
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
            created_at=datetime.now(UTC),
        )
        self._session.add(assistant)
        self._session.flush()
        return assistant

    def ask_adhoc(
        self,
        question: str,
        *,
        filters: RetrievalFilters | None = None,
    ) -> str:
        """Session-less Q&A: retrieve + generate without persisting messages."""
        if filters is None:
            filters = RetrievalFilters()
        chunks = self._retriever.retrieve(question, filters)

        # If the user applied a scope and nothing matched, say so explicitly
        # rather than letting the LLM hallucinate without grounding.
        if not chunks and _has_active_scope(filters):
            return "No indexed content matched the selected scope."

        result = generate_answer(
            self._ollama, AnswerRequest(question=question, chunks=chunks)
        )
        trailer = self._render_citations(chunks)
        if trailer:
            return f"{result.answer}\n\n{trailer}"
        return result.answer

    def _render_citations(self, chunks: list[RetrievedChunk]) -> str:
        """Format citations as "[ref · heading_path]" joined by spaces."""
        if not chunks:
            return ""
        reg_ids = {c.regulation_id for c in chunks}
        refs: dict[int, str] = {
            r.regulation_id: r.reference_number
            for r in (
                self._session.query(Regulation)
                .filter(Regulation.regulation_id.in_(reg_ids))
                .all()
            )
        }
        labels: list[str] = []
        seen: set[str] = set()
        for c in chunks:
            ref = refs.get(c.regulation_id, f"reg {c.regulation_id}")
            path = " > ".join(c.heading_path or [])
            label = f"[{ref} · {path}]" if path else f"[{ref}]"
            if label in seen:
                continue
            seen.add(label)
            labels.append(label)
        return "Sources: " + " ".join(labels)

    def list_messages(self, session_id: int) -> list[ChatMessage]:
        return (
            self._session.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at, ChatMessage.message_id)
            .all()
        )
