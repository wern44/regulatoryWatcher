"""Chunk long regulatory text into structure-aware segments for vector indexing."""
from __future__ import annotations

import re
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


# Level 0: Chapter (EN/FR/DE), Roman or Arabic numeral, with optional trailing text
_CHAPTER = re.compile(
    r"^\s*(?:Chapter|Chapitre|Kapitel)\s+[IVXLCM0-9]+\b.*$",
    re.MULTILINE | re.IGNORECASE,
)
# Level 1a: Article / Artikel + digit(s) with optional sub-letter
_ARTICLE = re.compile(
    r"^\s*(?:Article|Artikel)\s+\d+[a-z]?\b.*$",
    re.MULTILINE | re.IGNORECASE,
)
# Level 1b: § + digit (common in German law)
_PARAGRAPH_SYMBOL = re.compile(
    r"^\s*§\s*\d+[a-z]?\b.*$",
    re.MULTILINE,
)


def chunk_text(
    text: str, *, chunk_size_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    """Split text into chunks, preferring legal-structural boundaries.

    When Chapter/Article/§ boundaries are detected, emit one chunk per Article
    (preamble captured as its own heading-less chunk). When no structure is
    found, fall back to the legacy recursive splitter.
    """
    if not text or not text.strip():
        return []

    boundaries = _collect_boundaries(text)
    if not boundaries:
        return _recursive_fallback(text, chunk_size_tokens, overlap_tokens)

    boundaries.sort(key=lambda b: b[0])

    # Segments: list of (heading_path, body_text)
    segments: list[tuple[list[str], str]] = []

    # Preamble: text before the first boundary
    if boundaries[0][0] > 0:
        preamble = text[: boundaries[0][0]].strip()
        if preamble:
            segments.append(([], preamble))

    # Track active heading at each level; when we move into a new level, drop deeper ones.
    level_to_heading: dict[int, str] = {}

    for i, (pos, level, heading) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        # Update the heading cache: set this level, drop anything deeper.
        level_to_heading[level] = heading
        for deeper in [k for k in level_to_heading if k > level]:
            level_to_heading.pop(deeper)
        path = [level_to_heading[k] for k in sorted(level_to_heading)]
        body = text[pos:end].strip()
        # Skip heading-only segments (e.g. a Chapter line with no prose before
        # the next Article). The heading is preserved in the deeper segments'
        # heading_path, so no content is lost.
        if body and body.strip() != heading.strip():
            segments.append((list(path), body))

    return _build_chunks(segments, chunk_size_tokens, overlap_tokens)


def _collect_boundaries(text: str) -> list[tuple[int, int, str]]:
    """Return (position, level, heading_label) tuples for all detected boundaries."""
    matches: list[tuple[int, int, str]] = []
    for m in _CHAPTER.finditer(text):
        matches.append((m.start(), 0, m.group(0).strip()))
    for m in _ARTICLE.finditer(text):
        matches.append((m.start(), 1, m.group(0).strip()))
    for m in _PARAGRAPH_SYMBOL.finditer(text):
        matches.append((m.start(), 1, m.group(0).strip()))
    return matches


def _build_chunks(
    segments: list[tuple[list[str], str]],
    chunk_size_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    char_budget = chunk_size_tokens * 4
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=char_budget,
        chunk_overlap=overlap_tokens * 4,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks: list[Chunk] = []
    idx = 0
    for path, body in segments:
        if len(body) <= char_budget:
            tokens = len(_ENCODER.encode(body))
            chunks.append(Chunk(
                index=idx, text=body, token_count=tokens,
                embed_text=_prefix(path) + body,
                heading_path=list(path),
            ))
            idx += 1
        else:
            for piece in splitter.split_text(body):
                tokens = len(_ENCODER.encode(piece))
                chunks.append(Chunk(
                    index=idx, text=piece, token_count=tokens,
                    embed_text=_prefix(path) + piece,
                    heading_path=list(path),
                ))
                idx += 1
    return chunks


def _recursive_fallback(
    text: str, chunk_size_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size_tokens * 4,
        chunk_overlap=overlap_tokens * 4,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    pieces = splitter.split_text(text)
    return [
        Chunk(
            index=i,
            text=p,
            token_count=len(_ENCODER.encode(p)),
            embed_text=p,
            heading_path=[],
        )
        for i, p in enumerate(pieces)
    ]


def _prefix(path: list[str]) -> str:
    if not path:
        return ""
    return f"[{' | '.join(path)}]\n\n"
