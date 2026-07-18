import pytest

from fifa_analytics.assistant import llm


def test_unreachable_ollama_is_not_available(monkeypatch):
    monkeypatch.setattr(llm, "OLLAMA_URL", "http://127.0.0.1:9")  # discard port, nothing listens
    assert llm.is_available(timeout=0.5) is False
    assert llm.list_models() == []


def test_chat_raises_connection_error_with_setup_help(monkeypatch):
    monkeypatch.setattr(llm, "OLLAMA_URL", "http://127.0.0.1:9")
    with pytest.raises(ConnectionError, match="ollama.com"):
        llm.chat([{"role": "user", "content": "hi"}], timeout=0.5)
