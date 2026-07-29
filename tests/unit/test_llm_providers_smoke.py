from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from urllib.error import URLError
from urllib.request import Request

import pytest

from libs.llm import (
    AzureOpenAILLM,
    BaseVisionLLM,
    ChatResponse,
    DeepSeekLLM,
    ImageInput,
    LLMFactory,
    Message,
    OpenAICompatibleLLM,
)


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def response_payload(content: str = "answer", model: str = "model") -> dict[str, Any]:
    return {
        "model": model,
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def headers(request: Request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def test_openai_compatible_provider_posts_chat_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse(response_payload(content="pong", model="MiniMax-M3"))

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    llm = LLMFactory.create(
        {
            "llm": {
                "provider": "openai",
                "model": "MiniMax-M3",
                "base_url": "https://api.minimaxi.com/v1",
                "api_key": "secret",
            }
        }
    )

    result = llm.chat([Message(role="user", content="ping")], temperature=0)

    assert isinstance(result, ChatResponse)
    assert result.content == "pong"
    assert result.model == "MiniMax-M3"
    request = captured[0]
    assert request.full_url == "https://api.minimaxi.com/v1/chat/completions"
    assert headers(request)["authorization"] == "Bearer secret"
    assert json.loads(cast(bytes, request.data).decode("utf-8")) == {
        "model": "MiniMax-M3",
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0,
    }


def test_openai_compatible_provider_posts_multimodal_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse(response_payload(content="A flow diagram.", model="MiniMax-M3"))

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"image")
    llm = OpenAICompatibleLLM(
        {
            "model": "MiniMax-M3",
            "base_url": "https://api.minimaxi.com/v1",
            "api_key": "secret",
        }
    )

    result = llm.chat_with_image(
        "Describe this image.",
        ImageInput(path=image_path, mime_type="image/png"),
        messages=[Message(role="system", content="Be precise.")],
    )

    assert isinstance(llm, BaseVisionLLM)
    assert result.content == "A flow diagram."
    payload = json.loads(cast(bytes, captured[0].data).decode("utf-8"))
    assert payload == {
        "model": "MiniMax-M3",
        "messages": [
            {"role": "system", "content": "Be precise."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
                    },
                ],
            },
        ],
    }


def test_deepseek_provider_uses_openai_compatible_client() -> None:
    llm = LLMFactory.create(
        {"llm": {"provider": "deepseek", "model": "deepseek-chat", "api_key": "secret"}}
    )

    assert isinstance(llm, DeepSeekLLM)


def test_azure_provider_uses_deployment_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        captured.append(request)
        return FakeHTTPResponse(response_payload(content="azure answer", model="deployment"))

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    llm = LLMFactory.create(
        {
            "llm": {
                "provider": "azure",
                "endpoint": "https://example.openai.azure.com",
                "deployment_name": "gpt-4o",
                "api_version": "2024-02-15-preview",
                "api_key": "azure-secret",
            }
        }
    )

    result = llm.chat([Message(role="user", content="hi")])

    assert isinstance(llm, AzureOpenAILLM)
    assert result.content == "azure answer"
    request = captured[0]
    assert request.full_url == (
        "https://example.openai.azure.com/openai/deployments/gpt-4o/chat/completions"
        "?api-version=2024-02-15-preview"
    )
    assert headers(request)["api-key"] == "azure-secret"
    assert "model" not in json.loads(cast(bytes, request.data).decode("utf-8"))


def test_chat_rejects_bad_input_shape() -> None:
    llm = OpenAICompatibleLLM(
        {"model": "chat-model", "api_key": "secret", "base_url": "https://example.test/v1"}
    )

    with pytest.raises(ValueError, match="openai input error"):
        llm.chat([])
    with pytest.raises(ValueError, match=r"messages\[0\]"):
        llm.chat(cast(Any, [{"role": "user", "content": "hi"}]))


def test_chat_wraps_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Request, timeout: float) -> FakeHTTPResponse:
        raise URLError("down")

    monkeypatch.setattr("src.libs.llm.openai_llm.urlopen", fake_urlopen)
    llm = OpenAICompatibleLLM(
        {"model": "chat-model", "api_key": "secret", "base_url": "https://example.test/v1"}
    )

    with pytest.raises(RuntimeError, match="openai URLError"):
        llm.chat([Message(role="user", content="hi")])
