"""Structure-aware chunking for legal/regulatory text.

Three-level hierarchy preserving legal meaning:
  Level 0 — Title / Chapter  (for summary queries)
  Level 1 — Article / §      (primary retrieval unit)
  Level 2 — Paragraph         (for precise retrieval)

Each chunk carries:
  - heading_path:  hierarchical breadcrumb  [Chapter I, Article 5]
  - embed_text:    regulation metadata + heading_path + body (what gets embedded)
  - cross_refs:    articles referenced within the chunk text
  - is_definition: True when the chunk comes from a definitions section
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

_ENCODER = tiktoken.get_encoding("cl100k_base")

# ---------------------------------------------------------------------------
# Structural patterns (EN / FR / DE)
# ---------------------------------------------------------------------------

# Level 0: Title / Part / Chapter / Section / Sub-chapter
# Matches both Roman numerals (I, II, III) and Arabic with optional decimals (1, 1.2, 4.1.1)
_TITLE = re.compile(
    r"^\s*(?:Title|Titre|Titel|Part|Partie|Teil)\s+[IVXLCM0-9]+\b",
    re.IGNORECASE,
)
_CHAPTER = re.compile(
    r"^\s*(?:(?:Sub[- ]?)?Chapter|Chapitre|Kapitel|Section|Abschnitt)"
    r"\s+[IVXLCM0-9]+(?:\.\d+)*\b",
    re.IGNORECASE,
)

# Level 1: Article / Artikel / Art. / §
_ARTICLE = re.compile(
    r"^\s*(?:Article|Artikel|Art\.?)\s+\d+[a-z]?\b",
    re.IGNORECASE,
)
_PARAGRAPH_SYMBOL = re.compile(
    r"^\s*§\s*\d+[a-z]?\b",
)

# Cross-reference patterns within chunk text
_XREF = re.compile(
    r"(?:Article|Artikel|Art\.?)\s+(\d+(?:\(\d+\))?(?:\([a-z]\))?)"
    r"|§\s*(\d+[a-z]?)",
    re.IGNORECASE,
)

# Definition section heuristic (article title or body mentions "definitions")
_DEFINITION_RE = re.compile(
    r"\bD[eé]finitions?\b|\bBegriffs?bestimmung",
    re.IGNORECASE,
)


@dataclass
class Chunk:
    index: int
    text: str
    token_count: int
    embed_text: str = ""
    heading_path: list[str] = field(default_factory=list)
    cross_refs: list[str] = field(default_factory=list)
    is_definition: bool = False


def chunk_text(
    text: str,
    *,
    chunk_size_tokens: int,
    overlap_tokens: int,
    regulation_meta: str = "",
) -> list[Chunk]:
    """Split text into chunks, preferring legal-structural boundaries.

    Parameters
    ----------
    regulation_meta:
        Optional prefix like ``"CSSF 22/806 — Risk management — CSSF"``
        prepended to every chunk's ``embed_text`` for richer embeddings.
    """
    if not text or not text.strip():
        return []

    boundaries = _collect_boundaries(text)
    if not boundaries:
        return _recursive_fallback(
            text, chunk_size_tokens, overlap_tokens, regulation_meta,
        )

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

    return _build_chunks(segments, chunk_size_tokens, overlap_tokens, regulation_meta)


# ---------------------------------------------------------------------------
# Boundary detection
# ---------------------------------------------------------------------------

def _collect_boundaries(text: str) -> list[tuple[int, int, str]]:
    """Return (position, level, heading_label) tuples for all detected boundaries.

    Strategy 1 (clean text): When the text has blank-line-separated blocks,
    only emit a boundary when a heading is the first line of a block. This
    avoids false positives from mid-paragraph "Article N" references.

    Strategy 2 (PDF-extracted text): When there are very few blank-line blocks
    (common with pdfplumber output), fall back to line-by-line scanning. In
    this mode, a heading is accepted when the line starts at column 0 and the
    previous line is either empty or ends a sentence.
    """
    blocks = re.split(r"\n\s*\n", text)
    if len(blocks) > 3:
        # Strategy 1: block-based (original approach)
        return _boundaries_from_blocks(text, blocks)
    # Strategy 2: line-based (for PDF text without blank-line separation)
    return _boundaries_from_lines(text)


def _boundaries_from_blocks(
    text: str, blocks: list[str],
) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []
    cursor = 0
    for block in blocks:
        abs_pos = text.find(block, cursor)
        if abs_pos < 0:
            cursor += len(block)
            continue
        cursor = abs_pos + len(block)

        stripped = block.lstrip()
        if not stripped:
            continue
        first_line = stripped.split("\n", 1)[0]
        heading_pos = abs_pos + (len(block) - len(stripped))

        level = _classify_heading(first_line)
        if level is not None:
            matches.append((heading_pos, level, first_line.strip()))
    return matches


def _boundaries_from_lines(text: str) -> list[tuple[int, int, str]]:
    """Line-by-line heading detection for PDF-extracted text."""
    matches: list[tuple[int, int, str]] = []
    pos = 0
    prev_line = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            level = _classify_heading(stripped)
            if level is not None:
                # Accept as heading if previous line is blank/short or ends a sentence
                prev_s = prev_line.strip()
                looks_like_boundary = (
                    not prev_s
                    or prev_s.endswith((".", ":", ")"))
                    or len(prev_s) < 5
                )
                if looks_like_boundary:
                    matches.append((pos, level, stripped))
        prev_line = line
        pos += len(line) + 1  # +1 for the \n
    return matches


def _classify_heading(line: str) -> int | None:
    """Return the heading level (0 or 1) or None if not a heading."""
    if _TITLE.match(line):
        return 0
    if _CHAPTER.match(line):
        return 0
    if _ARTICLE.match(line) or _PARAGRAPH_SYMBOL.match(line):
        return 1
    return None


# ---------------------------------------------------------------------------
# Chunk building
# ---------------------------------------------------------------------------

def _build_chunks(
    segments: list[tuple[list[str], str]],
    chunk_size_tokens: int,
    overlap_tokens: int,
    regulation_meta: str,
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
        is_def = _is_definition_section(path, body)
        pieces = [body] if len(body) <= char_budget else splitter.split_text(body)
        for piece in pieces:
            tokens = len(_ENCODER.encode(piece))
            refs = _extract_cross_refs(piece)
            chunks.append(Chunk(
                index=idx,
                text=piece,
                token_count=tokens,
                embed_text=_embed_prefix(regulation_meta, path) + piece,
                heading_path=list(path),
                cross_refs=refs,
                is_definition=is_def,
            ))
            idx += 1
    return chunks


def _recursive_fallback(
    text: str,
    chunk_size_tokens: int,
    overlap_tokens: int,
    regulation_meta: str,
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
            embed_text=_embed_prefix(regulation_meta, []) + p,
            heading_path=[],
            cross_refs=_extract_cross_refs(p),
            is_definition=bool(_DEFINITION_RE.search(p)),
        )
        for i, p in enumerate(pieces)
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _embed_prefix(regulation_meta: str, path: list[str]) -> str:
    """Build the metadata prefix prepended to embed_text.

    Example output::

        CSSF 22/806 — Risk management — CSSF, Chapter II, Article 5:
        [actual chunk text]
    """
    parts: list[str] = []
    if regulation_meta:
        parts.append(regulation_meta)
    if path:
        parts.append(", ".join(path))
    if parts:
        return ", ".join(parts) + ":\n"
    return ""


def _extract_cross_refs(text: str) -> list[str]:
    """Extract cross-referenced article/§ identifiers from chunk text."""
    refs: list[str] = []
    seen: set[str] = set()
    for m in _XREF.finditer(text):
        ref = m.group(1) or m.group(2)
        if ref and ref not in seen:
            refs.append(ref)
            seen.add(ref)
    return refs


def _is_definition_section(path: list[str], body: str) -> bool:
    """Heuristic: is this chunk from a definitions article/section?"""
    for heading in path:
        if _DEFINITION_RE.search(heading):
            return True
    # Check the first 200 chars of body for "Definitions" as a heading
    return bool(_DEFINITION_RE.search(body[:200]))
