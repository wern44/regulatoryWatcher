from regwatch.pipeline.diff import compute_diff


def test_returns_none_for_identical_texts() -> None:
    assert compute_diff("hello world", "hello world") is None


def test_generates_unified_diff_for_changes() -> None:
    old = "line one\nline two\nline three\n"
    new = "line one\nline two modified\nline three\n"
    result = compute_diff(old, new)
    assert result is not None
    assert "-line two" in result
    assert "+line two modified" in result


def test_handles_added_and_removed_lines() -> None:
    old = "a\nb\nc\n"
    new = "a\nb\nc\nd\n"
    result = compute_diff(old, new)
    assert result is not None
    assert "+d" in result
