"""Generate grounded answers from retrieved chunks via Ollama."""
from __future__ import annotations

from dataclasses import dataclass

from regwatch.llm.client import LLMClient
from regwatch.rag.retrieval import RetrievedChunk

_SYSTEM_PROMPT = (
    "You are a regulatory assistant for a Luxembourg fund management company. "
    "Answer ONLY using the context provided below. "
    "If the context does not contain the answer, say "
    "'The provided context does not contain an answer.' "
    "Cite sources in your answer as (chunk <chunk_id>)."
)


@dataclass
class AnswerRequest:
    question: str
    chunks: list[RetrievedChunk]


@dataclass
class AnswerResponse:
    answer: str
    cited_chunk_ids: list[int]


def generate_answer(
    ollama: LLMClient, request: AnswerRequest
) -> AnswerResponse:
    if not request.chunks:
        return AnswerResponse(
            answer="I could not find relevant information in the indexed regulations.",
            cited_chunk_ids=[],
        )

    context_blocks = "\n\n".join(
        f"[chunk {c.chunk_id} | regulation_id={c.regulation_id}]\n{c.text}"
        for c in request.chunks
    )
    user_prompt = f"Context:\n{context_blocks}\n\nQuestion: {request.question}"

    answer = ollama.chat(system=_SYSTEM_PROMPT, user=user_prompt)
    cited_ids = [c.chunk_id for c in request.chunks]
    return AnswerResponse(answer=answer, cited_chunk_ids=cited_ids)
