from __future__ import annotations

import json
from typing import Any, cast
from urllib.error import URLError
from urllib.request import Request

import pytest

from libs.llm import ChatResponse, LLMFactory, Message, OllamaLLM


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_ollama_provider_posts_chat_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse(
            {
                "model": "llama3.2",
                "message": {"role": "assistant", "content": "local answer"},
                "prompt_eval_count": 3,
                "eval_count": 5,
            }
        )

    monkeypatch.setattr("src.libs.llm.ollama_llm.urlopen", fake_urlopen)
    llm = LLMFactory.create({"llm": {"provider": "ollama", "model": "llama3.2"}})

    result = llm.chat([Message(role="user", content="hi")], keep_alive="5m")

    assert isinstance(llm, OllamaLLM)
    assert isinstance(result, ChatResponse)
    assert result.content == "local answer"
    assert result.usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
    request = captured[0]
    assert request.full_url == "http://localhost:11434/api/chat"
    assert json.loads(cast(bytes, request.data).decode("utf-8")) == {
        "model": "llama3.2",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
        "keep_alive": "5m",
    }


def test_ollama_provider_accepts_custom_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse({"message": {"content": "ok"}})

    monkeypatch.setattr("src.libs.llm.ollama_llm.urlopen", fake_urlopen)
    llm = LLMFactory.create(
        {
            "llm": {
                "provider": "ollama",
                "model": "qwen2.5",
                "base_url": "http://127.0.0.1:11434/",
            }
        }
    )

    assert llm.chat([Message(role="user", content="hi")]).content == "ok"
    assert captured[0].full_url == "http://127.0.0.1:11434/api/chat"


def test_ollama_rejects_bad_input_shape() -> None:
    llm = OllamaLLM({"model": "llama3.2"})

    with pytest.raises(ValueError, match="ollama input error"):
        llm.chat([])
    with pytest.raises(ValueError, match=r"messages\[0\]"):
        llm.chat(cast(Any, [{"role": "user", "content": "hi"}]))


def test_ollama_wraps_transport_errors_without_sensitive_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        raise URLError("connection refused")

    monkeypatch.setattr("src.libs.llm.ollama_llm.urlopen", fake_urlopen)
    llm = OllamaLLM({"model": "llama3.2", "api_key": "secret-should-not-appear"})

    with pytest.raises(RuntimeError, match="ollama URLError") as exc_info:
        llm.chat([Message(role="user", content="hi")])
    assert "secret-should-not-appear" not in str(exc_info.value)
