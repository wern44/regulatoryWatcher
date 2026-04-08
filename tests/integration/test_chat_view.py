from pathlib import Path
from unittest.mock import MagicMock

from tests.integration.test_app_smoke import _client


def test_chat_list_renders(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    r = client.get("/chat")
    assert r.status_code == 200
    assert "Q&amp;A" in r.text or "Q&A" in r.text


def test_chat_create_and_ask_flow(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    # Replace the app's ollama client with a mock that also handles embed
    # (for retrieval). Embeddings aren't used since no indexed content exists.
    fake = MagicMock()
    # Production config uses 768 dims (nomic-embed-text).
    fake.embed.return_value = [0.0] * 768
    fake.chat.return_value = "Sample assistant reply."
    client.app.state.ollama_client = fake

    # Create a new session.
    r = client.post("/chat", data={"title": "DORA questions"}, follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]

    # Load the session page.
    r2 = client.get(location)
    assert r2.status_code == 200
    assert "DORA questions" in r2.text

    # Ask a question.
    r3 = client.post(
        location + "/ask",
        data={"question": "What is DORA"},
        follow_redirects=True,
    )
    assert r3.status_code == 200
    # With no indexed content the generator returns the no-results message.
    assert "could not find" in r3.text.lower()
    assert "What is DORA" in r3.text
