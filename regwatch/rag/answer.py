"""Generate grounded answers from retrieved chunks via Ollama."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from regwatch.llm.client import LLMClient, context_limit_from_error
from regwatch.rag.retrieval import RetrievedChunk

# Conservative: ~4 characters per token; keep room for the system prompt,
# the question and the answer.
_CHARS_PER_TOKEN = 4
_RESERVED_TOKENS = 1500

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
    if chunk.is_expansion:
        header += " | context"
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

    # The model's context window is unknown until it rejects a prompt
    # (LM Studio: HTTP 400 naming n_ctx). Then keep the best-ranked chunks
    # that fit and retry.
    chunks = request.chunks
    while True:
        context_blocks = "\n\n".join(_format_context_block(c) for c in chunks)
        user_prompt = f"Context:\n{context_blocks}\n\nQuestion: {request.question}"
        try:
            answer = ollama.chat(system=_SYSTEM_PROMPT, user=user_prompt)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400 or len(chunks) == 1:
                raise
            n_ctx = context_limit_from_error(e.response.text)
            fitted = _fit_to_context(chunks, n_ctx) if n_ctx else []
            chunks = fitted if 0 < len(fitted) < len(chunks) else chunks[: len(chunks) // 2]
            continue
        return AnswerResponse(
            answer=answer, cited_chunk_ids=[c.chunk_id for c in chunks]
        )


def _fit_to_context(chunks: list[RetrievedChunk], n_ctx: int) -> list[RetrievedChunk]:
    """The leading chunks whose formatted text fits a window of ``n_ctx`` tokens."""
    budget = (n_ctx - _RESERVED_TOKENS) * _CHARS_PER_TOKEN
    fitted: list[RetrievedChunk] = []
    used = 0
    for chunk in chunks:
        used += len(_format_context_block(chunk)) + 2
        if used > budget:
            break
        fitted.append(chunk)
    return fitted or chunks[:1]
