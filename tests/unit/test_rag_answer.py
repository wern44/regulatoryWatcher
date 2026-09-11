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


def _chunk(i: int, size: int = 2000) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=i, version_id=1, regulation_id=1, text="x" * size,
        is_ict=False, lifecycle_stage="IN_FORCE", score=1.0 / i,
    )


def _http_400(body: str):  # type: ignore[no-untyped-def]
    import httpx

    request = httpx.Request("POST", "http://llm/v1/chat/completions")
    return httpx.HTTPStatusError(
        "400", request=request, response=httpx.Response(400, text=body, request=request)
    )


def test_generate_answer_fits_context_to_the_model_window() -> None:
    """LM Studio runs the chat model with n_ctx=4096; 20 retrieved chunks of
    ~500 tokens don't fit and the request failed with HTTP 400."""
    ollama = MagicMock()
    ollama.chat.side_effect = [
        _http_400('{"error":"... (n_keep: 12015>= n_ctx: 4096). Try ..."}'),
        "Fits now (chunk 1).",
    ]
    chunks = [_chunk(i) for i in range(1, 21)]

    response = generate_answer(ollama, AnswerRequest(question="Q?", chunks=chunks))

    assert response.answer == "Fits now (chunk 1)."
    retry_prompt = ollama.chat.call_args_list[1].kwargs["user"]
    assert len(retry_prompt) < 4096 * 4
    assert response.cited_chunk_ids == list(range(1, len(response.cited_chunk_ids) + 1))
    assert 1 <= len(response.cited_chunk_ids) < 20


def test_generate_answer_halves_context_when_limit_unknown() -> None:
    ollama = MagicMock()
    ollama.chat.side_effect = [_http_400("bad request"), "ok"]

    response = generate_answer(
        ollama, AnswerRequest(question="Q?", chunks=[_chunk(i) for i in range(1, 9)])
    )

    assert response.cited_chunk_ids == [1, 2, 3, 4]


def test_generate_answer_reraises_other_http_errors() -> None:
    import httpx
    import pytest

    request = httpx.Request("POST", "http://llm")
    ollama = MagicMock()
    ollama.chat.side_effect = httpx.HTTPStatusError(
        "500", request=request, response=httpx.Response(500, request=request)
    )
    with pytest.raises(httpx.HTTPStatusError):
        generate_answer(ollama, AnswerRequest(question="Q?", chunks=[_chunk(1)]))
