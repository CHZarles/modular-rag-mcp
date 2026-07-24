"""Tests for B7.2: Ollama LLM (mock HTTP)."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.settings import LLMConfig
from src.libs.llm._http import LLMHTTPError
from src.libs.llm.llm_factory import LLMFactory
from src.libs.llm.ollama_llm import OllamaLLM
from src.ports.llm import ChatResponse, Message


def _ok_response(content: str = "ollama answer") -> dict[str, Any]:
    return {
        "model": "llama3",
        "message": {"role": "assistant", "content": content},
        "done": True,
    }


def test_ollama_llm_returns_chat_response() -> None:
    llm = OllamaLLM(
        LLMConfig(provider="ollama", model="llama3"),
        transport=lambda u, b, h: _ok_response(),
    )
    response = llm.chat([Message(role="user", content="hi")])
    assert isinstance(response, ChatResponse)
    assert response.content == "ollama answer"
    assert response.model == "llama3"
    assert response.usage is None


def test_ollama_llm_uses_localhost_by_default() -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["url"] = url
        return _ok_response()

    llm = OllamaLLM(LLMConfig(provider="ollama"), transport=transport)
    llm.chat([Message(role="user", content="hi")])
    assert captured["url"].startswith("http://localhost:11434/api/chat")


def test_ollama_llm_accepts_custom_base_url() -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["url"] = url
        return _ok_response()

    llm = OllamaLLM(
        LLMConfig(provider="ollama"),
        base_url="http://gpu-host:11434",
        transport=transport,
    )
    llm.chat([Message(role="user", content="hi")])
    assert captured["url"].startswith("http://gpu-host:11434/api/chat")


def test_ollama_llm_empty_messages_raises() -> None:
    llm = OllamaLLM(
        LLMConfig(provider="ollama"), transport=lambda u, b, h: _ok_response()
    )
    with pytest.raises(ValueError, match="messages must not be empty"):
        llm.chat([])


def test_ollama_llm_malformed_response_raises_http_error() -> None:
    def transport(url, body, headers):
        return {"done": True}  # missing message

    llm = OllamaLLM(LLMConfig(provider="ollama"), transport=transport)
    with pytest.raises(LLMHTTPError, match="malformed response"):
        llm.chat([Message(role="user", content="hi")])


def test_ollama_llm_connection_error_does_not_leak_base_url() -> None:
    """Connection failure must not echo back the user's base_url or api_key."""
    leaked: list[str] = []

    def transport(url, body, headers):
        leaked.append(url)
        raise LLMHTTPError("network error: Connection refused (http://secret-host:11434)")

    llm = OllamaLLM(
        LLMConfig(provider="ollama", api_key="very-secret"),
        base_url="http://secret-host:11434",
        transport=transport,
    )
    with pytest.raises(LLMHTTPError) as ei:
        llm.chat([Message(role="user", content="hi")])
    msg = str(ei.value)
    assert "secret-host" not in msg
    assert "very-secret" not in msg


def test_factory_routes_to_ollama_llm() -> None:
    snapshot = dict(LLMFactory._registry)
    LLMFactory.reset()
    try:
        LLMFactory.register(
            "ollama",
            lambda c: OllamaLLM(c, transport=lambda u, b, h: _ok_response()),
        )
        llm = LLMFactory.create(LLMConfig(provider="ollama"))
        assert isinstance(llm, OllamaLLM)
    finally:
        LLMFactory._registry.clear()
        LLMFactory._registry.update(snapshot)