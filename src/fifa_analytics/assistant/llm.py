"""Thin Ollama client — the spec's free, fully local LLM runtime
(https://ollama.com). No API keys, no cloud, nothing leaves the machine.

The dashboard degrades gracefully when Ollama isn't running: the context
pack still renders as data, only the prose answer needs the model."""

import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

SETUP_HELP = """\
Ollama isn't reachable. To enable the assistant's answers (one-time setup):

1. Install Ollama from https://ollama.com/download (free)
2. Pull a model: `ollama pull llama3.2` (~2GB; `llama3.2:1b` is a lighter option)
3. Ollama runs automatically after install — then just re-ask here.

Everything stays on your machine: no account, no API key, no data sent anywhere.
"""


def is_available(timeout: float = 2.0) -> bool:
    try:
        return requests.get(f"{OLLAMA_URL}/api/tags", timeout=timeout).ok
    except requests.RequestException:
        return False


def list_models() -> list[str]:
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        return sorted(m["name"] for m in resp.json().get("models", []))
    except requests.RequestException:
        return []


def chat(messages: list[dict], model: str = DEFAULT_MODEL, timeout: float = 300.0) -> str:
    """One non-streamed chat completion. Raises ConnectionError with setup
    help when Ollama isn't running."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as request_error:
        raise ConnectionError(SETUP_HELP) from request_error
    return resp.json()["message"]["content"]
