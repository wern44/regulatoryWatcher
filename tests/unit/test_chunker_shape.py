from regwatch.rag.chunker import Chunk, chunk_text


def test_chunk_has_embed_text_and_heading_path():
    chunks = chunk_text("one two three", chunk_size_tokens=100, overlap_tokens=10)
    assert chunks  # at least one chunk
    assert all(isinstance(c, Chunk) for c in chunks)
    assert all(isinstance(c.embed_text, str) for c in chunks)
    assert all(isinstance(c.heading_path, list) for c in chunks)


def test_chunk_embed_text_defaults_to_text():
    """Until D2 adds structure-aware chunking, embed_text should equal text."""
    chunks = chunk_text("simple paragraph.", chunk_size_tokens=100, overlap_tokens=10)
    assert chunks
    for c in chunks:
        assert c.embed_text == c.text


def test_chunk_heading_path_defaults_to_empty():
    """Until D2 adds structure detection, heading_path is empty for plain text."""
    chunks = chunk_text("simple paragraph.", chunk_size_tokens=100, overlap_tokens=10)
    assert chunks
    for c in chunks:
        assert c.heading_path == []
