from unittest.mock import MagicMock

from regwatch.rag.answer import AnswerRequest, generate_answer
from regwatch.rag.retrieval import RetrievedChunk


def test_generate_answer_with_chunks() -> None:
    chunks = [
        RetrievedChunk(
            chunk_id=1,
            version_id=10,
            regulation_id=100,
            text="Article 24 of DORA requires ICT risk assessments.",
            is_ict=True,
            lifecycle_stage="IN_FORCE",
            score=0.9,
        )
    ]
    ollama = MagicMock()
    ollama.chat.return_value = (
        "Under Article 24 of DORA, ICT risk assessments are required (chunk 1)."
    )

    req = AnswerRequest(
        question="What does Article 24 DORA require?", chunks=chunks
    )
    response = generate_answer(ollama, req)

    assert "Article 24" in response.answer
    assert response.cited_chunk_ids == [1]


def test_generate_answer_declines_without_chunks() -> None:
    ollama = MagicMock()
    req = AnswerRequest(question="Anything?", chunks=[])
    response = generate_answer(ollama, req)

    assert "could not find" in response.answer.lower()
    ollama.chat.assert_not_called()
    assert response.cited_chunk_ids == []
