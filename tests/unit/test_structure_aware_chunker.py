from regwatch.rag.chunker import chunk_text


EN_SAMPLE = """
Chapter I — General provisions

Article 1
This Regulation lays down rules for a wide range of activities.

Article 2
Definitions.
(1) 'person' means a natural person.
(2) 'entity' means a legal person.
"""

FR_SAMPLE = """
Chapitre II — Dispositions générales

Article 3
Le présent règlement établit les règles pour...
"""

DE_SAMPLE = """
Kapitel IV — Allgemeine Bestimmungen

§ 1 Allgemeines
Dieses Gesetz regelt...

§ 2 Anwendungsbereich
Dieses Gesetz gilt für...
"""


def test_splits_on_article_boundaries_en():
    chunks = chunk_text(EN_SAMPLE, chunk_size_tokens=1000, overlap_tokens=50)
    texts = [c.text for c in chunks]
    # At least two chunks, one per Article
    assert len(chunks) >= 2
    assert any("Article 1" in t for t in texts)
    assert any("Article 2" in t for t in texts)
    # Heading path includes Chapter + Article
    article_2_chunks = [c for c in chunks if "Article 2" in c.text]
    assert article_2_chunks
    path_str = " | ".join(article_2_chunks[0].heading_path)
    assert "Chapter I" in path_str
    assert "Article 2" in path_str


def test_embed_text_has_metadata_prefix_when_heading_path_is_nonempty():
    chunks = chunk_text(EN_SAMPLE, chunk_size_tokens=1000, overlap_tokens=50)
    structured = [c for c in chunks if c.heading_path]
    assert structured
    for c in structured:
        # The embed_text should start with a prefix containing the heading
        assert c.text in c.embed_text
        assert c.embed_text.index(c.text) > 0  # prefix before text


def test_embed_text_includes_regulation_meta():
    chunks = chunk_text(
        EN_SAMPLE, chunk_size_tokens=1000, overlap_tokens=50,
        regulation_meta="CSSF 22/806 — Risk management — CSSF",
    )
    structured = [c for c in chunks if c.heading_path]
    assert structured
    for c in structured:
        assert "CSSF 22/806" in c.embed_text
        assert c.text in c.embed_text


def test_falls_back_to_recursive_on_unstructured_text():
    text = "word " * 500  # no article boundaries
    chunks = chunk_text(text, chunk_size_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 1
    # No structure detected → heading_path empty
    for c in chunks:
        assert c.heading_path == []


def test_french_articles():
    chunks = chunk_text(FR_SAMPLE, chunk_size_tokens=1000, overlap_tokens=50)
    assert any("Article 3" in c.text for c in chunks)
    # heading path includes the chapter heading
    article_chunks = [c for c in chunks if "Article 3" in c.text]
    assert article_chunks
    assert any("Chapitre II" in h for h in article_chunks[0].heading_path)


def test_german_paragraphs():
    chunks = chunk_text(DE_SAMPLE, chunk_size_tokens=1000, overlap_tokens=50)
    texts = [c.text for c in chunks]
    assert any("§ 1" in t for t in texts)
    assert any("§ 2" in t for t in texts)
    # heading path includes both Kapitel and § marker
    para_2_chunks = [c for c in chunks if "§ 2" in c.text]
    assert para_2_chunks
    path_str = " | ".join(para_2_chunks[0].heading_path)
    assert "Kapitel IV" in path_str


def test_oversized_article_splits_within_boundary():
    """An Article longer than chunk_size_tokens is split, but sub-chunks share heading_path."""
    big_article = (
        "Chapter I — Overview\n\n"
        "Article 1\n"
        + ("Very long content. " * 500)
    )
    chunks = chunk_text(big_article, chunk_size_tokens=200, overlap_tokens=20)
    # Should produce multiple chunks, all with the same heading_path
    assert len(chunks) > 1
    article_chunks = [c for c in chunks if c.heading_path]
    assert len(article_chunks) > 1
    paths = {tuple(c.heading_path) for c in article_chunks}
    assert len(paths) == 1  # all sub-chunks share the same heading path


def test_mid_paragraph_article_reference_is_not_a_boundary():
    """Text with a newline followed by 'Article N' in the middle of a sentence
    should NOT create a new chunk — only headings isolated by blank lines do."""
    text = (
        "Chapter I — Overview\n\n"
        "Article 1\n"
        "This provision references\nArticle 17 of the older law and then continues with more text.\n\n"
        "Article 2\n"
        "Another provision."
    )
    chunks = chunk_text(text, chunk_size_tokens=1000, overlap_tokens=50)
    texts = [c.text for c in chunks]
    # Article 1's body should contain the "Article 17" reference intact
    assert any("Article 17 of the older law" in t for t in texts), (
        f"mid-paragraph Article reference was wrongly split. Chunks: {texts}"
    )
    # Exactly Article 1 and Article 2 chunks under Chapter I
    assert sum(
        1
        for c in chunks
        if c.heading_path and c.heading_path[-1].startswith("Article ")
    ) == 2


def test_preamble_chunk_has_no_heading_path():
    """Text before the first structural boundary is captured as a preamble."""
    text = (
        "This document introduces some content that appears before any article or chapter.\n\n"
        "Article 1\n"
        "The first provision."
    )
    chunks = chunk_text(text, chunk_size_tokens=1000, overlap_tokens=50)
    # There should be a chunk corresponding to the preamble
    preamble_chunks = [c for c in chunks if "introduces some content" in c.text]
    assert preamble_chunks
    # Preamble should have empty or no heading path (since it precedes the first boundary)
    assert preamble_chunks[0].heading_path == []


# ----- New tests for enhanced chunker features -----


def test_cross_refs_detected():
    """Cross-references to other articles are extracted."""
    text = (
        "Article 1\n"
        "This applies as defined in Article 4(1) and pursuant to § 5.\n\n"
        "Article 2\n"
        "See Article 1."
    )
    chunks = chunk_text(text, chunk_size_tokens=1000, overlap_tokens=50)
    art1 = [c for c in chunks if "Article 1" in c.heading_path[-1:]]
    assert art1
    # Should detect Article 4(1) and § 5 as cross-refs
    refs = art1[0].cross_refs
    assert "4(1)" in refs
    assert "5" in refs


def test_definition_section_flagged():
    """Chunks from a definitions article are flagged."""
    text = (
        "Chapter I — General\n\n"
        "Article 1 — Definitions\n"
        "(1) 'fund' means an investment fund.\n"
        "(2) 'manager' means a fund manager.\n\n"
        "Article 2 — Scope\n"
        "This regulation applies to all managers."
    )
    chunks = chunk_text(text, chunk_size_tokens=1000, overlap_tokens=50)
    def_chunks = [c for c in chunks if c.is_definition]
    non_def = [c for c in chunks if not c.is_definition]
    assert def_chunks, "Definition chunk not detected"
    assert any("fund" in c.text for c in def_chunks)
    assert non_def, "Non-definition chunks should exist"


def test_title_level_0_detected():
    """Title headings (Title I, Titre II) are detected at level 0."""
    text = (
        "Title I — General\n\n"
        "Article 1\n"
        "First provision.\n\n"
        "Title II — Specific\n\n"
        "Article 2\n"
        "Second provision."
    )
    chunks = chunk_text(text, chunk_size_tokens=1000, overlap_tokens=50)
    art1 = [c for c in chunks if "Article 1" in c.text and c.heading_path]
    art2 = [c for c in chunks if "Article 2" in c.text and c.heading_path]
    assert art1 and "Title I" in art1[0].heading_path[0]
    assert art2 and "Title II" in art2[0].heading_path[0]


def test_section_heading_detected():
    """'Section' headings are detected at level 0 (same as Chapter)."""
    text = (
        "Section I — Scope\n\n"
        "Article 1\n"
        "Applies to everything."
    )
    chunks = chunk_text(text, chunk_size_tokens=1000, overlap_tokens=50)
    art = [c for c in chunks if "Article 1" in c.text and c.heading_path]
    assert art
    assert "Section I" in art[0].heading_path[0]


def test_art_dot_abbreviation_detected():
    """'Art. 5' style headings are detected as article boundaries."""
    text = (
        "Art. 5 Scope\n\n"
        "This article defines the scope.\n\n"
        "Art. 6 Definitions\n\n"
        "(a) 'entity' means a legal person."
    )
    chunks = chunk_text(text, chunk_size_tokens=1000, overlap_tokens=50)
    texts = [c.text for c in chunks]
    assert any("Art. 5" in t for t in texts)
    assert any("Art. 6" in t for t in texts)
