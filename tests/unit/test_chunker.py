from regwatch.rag.chunker import chunk_text


def test_chunks_short_text_into_one_chunk() -> None:
    chunks = chunk_text("Hello world.", chunk_size_tokens=500, overlap_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].text == "Hello world."


def test_chunks_long_text_with_overlap() -> None:
    paragraphs = "\n\n".join(f"Paragraph {i}. " + "word " * 100 for i in range(20))
    chunks = chunk_text(paragraphs, chunk_size_tokens=200, overlap_tokens=30)
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk.index == i
        assert chunk.token_count > 0
        assert chunk.token_count <= 250


def test_returns_empty_for_empty_text() -> None:
    assert chunk_text("", chunk_size_tokens=500, overlap_tokens=50) == []
    assert chunk_text("   ", chunk_size_tokens=500, overlap_tokens=50) == []
