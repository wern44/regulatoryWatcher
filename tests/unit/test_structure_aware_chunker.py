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
        assert c.embed_text.startswith("[")
        assert "]\n\n" in c.embed_text
        # The original text is still in embed_text after the prefix
        assert c.text in c.embed_text


def test_falls_back_to_recursive_on_unstructured_text():
    text = "word " * 500  # no article boundaries
    chunks = chunk_text(text, chunk_size_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 1
    # No structure detected → heading_path empty, embed_text equals text
    for c in chunks:
        assert c.heading_path == []
        assert c.embed_text == c.text


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
