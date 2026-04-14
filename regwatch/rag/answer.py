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


def _format_context_block(chunk: RetrievedChunk) -> str:
    """Format a chunk for the LLM prompt, including heading_path if present."""
    header = f"[chunk {chunk.chunk_id} | regulation_id={chunk.regulation_id}"
    if chunk.heading_path:
        header += f" | {' > '.join(chunk.heading_path)}"
    header += "]"
    return f"{header}\n{chunk.text}"


def generate_answer(
    ollama: LLMClient, request: AnswerRequest
) -> AnswerResponse:
    if not request.chunks:
        return AnswerResponse(
            answer="I could not find relevant information in the indexed regulations.",
            cited_chunk_ids=[],
        )

    context_blocks = "\n\n".join(
        _format_context_block(c) for c in request.chunks
    )
    user_prompt = f"Context:\n{context_blocks}\n\nQuestion: {request.question}"

    answer = ollama.chat(system=_SYSTEM_PROMPT, user=user_prompt)
    cited_ids = [c.chunk_id for c in request.chunks]
    return AnswerResponse(answer=answer, cited_chunk_ids=cited_ids)
