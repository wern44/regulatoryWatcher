from unittest.mock import MagicMock

from regwatch.pipeline.match.ollama_refs import extract_references


def test_extracts_structured_refs_from_ollama() -> None:
    fake_client = MagicMock()
    fake_client.chat.return_value = (
        '[{"ref": "CSSF 18/698", "context": "amendments"}, '
        '{"ref": "2022/2554", "context": "DORA"}]'
    )

    refs = extract_references(
        fake_client, "this text amends CSSF 18/698 and refers to 2022/2554"
    )

    assert len(refs) == 2
    assert refs[0]["ref"] == "CSSF 18/698"
    assert refs[1]["ref"] == "2022/2554"


def test_returns_empty_on_invalid_json() -> None:
    fake_client = MagicMock()
    fake_client.chat.return_value = "not valid json"
    assert extract_references(fake_client, "something") == []


def test_returns_empty_on_empty_input() -> None:
    fake_client = MagicMock()
    assert extract_references(fake_client, "") == []
    fake_client.chat.assert_not_called()
