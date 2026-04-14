"""Chunk long regulatory text into overlapping segments for vector indexing."""
from __future__ import annotations

from dataclasses import dataclass, field

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

_ENCODER = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    index: int
    text: str
    token_count: int
    embed_text: str = ""
    heading_path: list[str] = field(default_factory=list)


def chunk_text(
    text: str, *, chunk_size_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    if not text or not text.strip():
        return []

    # langchain splitter works on characters, so convert tokens -> rough char budget.
    # ~4 characters per token is a safe heuristic for European-language text.
    chunk_size_chars = chunk_size_tokens * 4
    overlap_chars = overlap_tokens * 4

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size_chars,
        chunk_overlap=overlap_chars,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    pieces = splitter.split_text(text)

    chunks: list[Chunk] = []
    for i, piece in enumerate(pieces):
        tokens = len(_ENCODER.encode(piece))
        chunks.append(Chunk(
            index=i, text=piece, token_count=tokens,
            embed_text=piece, heading_path=[],
        ))
    return chunks
