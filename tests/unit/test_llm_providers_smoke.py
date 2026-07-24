"""Smoke tests for B7.1: OpenAI / Azure / DeepSeek providers (mock HTTP)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.core.settings import LLMConfig
from src.libs.llm._http import LLMHTTPError
from src.libs.llm.azure_llm import AzureLLM
from src.libs.llm.deepseek_llm import DeepSeekLLM
from src.libs.llm.llm_factory import LLMFactory
from src.libs.llm.openai_llm import OpenAILLM
from src.ports.llm import BaseLLM, ChatResponse, Message


def _fake_transport(payload: dict[str, Any]):
    """Build a transport that returns ``payload`` for any request."""

    def transport(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        # Echo parsed body back into the response for sanity in some tests.
        if "echo_request" in payload:
            return {"echo": json.loads(body), **payload}
        return payload

    return transport


def _ok_response(content: str = "hello back") -> dict[str, Any]:
    return {
        "id": "cmpl-1",
        "model": "gpt-4o-mini",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


def test_openai_llm_returns_chat_response() -> None:
    cfg = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-test")
    llm = OpenAILLM(cfg, transport=_fake_transport(_ok_response()))

    response = llm.chat([Message(role="user", content="hi")])

    assert isinstance(response, ChatResponse)
    assert response.content == "hello back"
    assert response.model == "gpt-4o-mini"
    assert response.usage == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


def test_openai_llm_sends_bearer_auth() -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        captured["url"] = url
        captured["headers"] = headers
        return _ok_response()

    llm = OpenAILLM(
        LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-abc"),
        transport=transport,
    )
    llm.chat([Message(role="user", content="x")])

    assert captured["headers"]["Authorization"] == "Bearer sk-abc"
    assert captured["url"].endswith("/chat/completions")


def test_openai_llm_empty_messages_raises_value_error() -> None:
    llm = OpenAILLM(
        LLMConfig(provider="openai"), transport=_fake_transport(_ok_response())
    )
    with pytest.raises(ValueError, match="messages must not be empty"):
        llm.chat([])


def test_openai_llm_invalid_role_raises_value_error() -> None:
    llm = OpenAILLM(
        LLMConfig(provider="openai"), transport=_fake_transport(_ok_response())
    )
    # Message is a frozen dataclass; build an equivalent object with a bogus role.
    bad = Message(role="user", content="x")
    object.__setattr__(bad, "role", "tool")
    with pytest.raises(ValueError, match="invalid role"):
        llm.chat([bad])


def test_openai_llm_blank_content_raises_value_error() -> None:
    llm = OpenAILLM(
        LLMConfig(provider="openai"), transport=_fake_transport(_ok_response())
    )
    with pytest.raises(ValueError, match="non-empty string"):
        llm.chat([Message(role="user", content="")])


def test_openai_llm_malformed_response_raises_http_error() -> None:
    def transport(url, body, headers):
        return {"choices": []}  # missing message

    llm = OpenAILLM(LLMConfig(provider="openai"), transport=transport)
    with pytest.raises(LLMHTTPError, match="malformed response"):
        llm.chat([Message(role="user", content="hi")])


def test_openai_llm_transport_failure_wraps_with_provider_name() -> None:
    def transport(url, body, headers):
        raise LLMHTTPError("network error: refused")

    llm = OpenAILLM(LLMConfig(provider="openai"), transport=transport)
    with pytest.raises(LLMHTTPError, match="openai chat failed"):
        llm.chat([Message(role="user", content="hi")])


# ---------------------------------------------------------------------------
# Azure provider
# ---------------------------------------------------------------------------


def test_azure_llm_requires_endpoint() -> None:
    with pytest.raises(ValueError, match="azure_endpoint"):
        AzureLLM(LLMConfig(provider="azure"))


def test_azure_llm_uses_api_key_header() -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        captured["url"] = url
        captured["headers"] = headers
        return _ok_response()

    llm = AzureLLM(
        LLMConfig(
            provider="azure",
            model="gpt-4o",
            azure_endpoint="https://example.azure.com",
            api_key="azure-key",
        ),
        transport=transport,
    )
    llm.chat([Message(role="user", content="hi")])

    assert "api-key=azure-key" not in captured["url"]  # not in URL
    assert captured["headers"]["api-key"] == "azure-key"
    assert "/openai/deployments/gpt-4o/chat/completions" in captured["url"]
    assert "api-version=" in captured["url"]


def test_azure_llm_returns_chat_response() -> None:
    llm = AzureLLM(
        LLMConfig(
            provider="azure",
            model="gpt-4o",
            azure_endpoint="https://example.azure.com",
        ),
        transport=_fake_transport(_ok_response("azure answer")),
    )
    response = llm.chat([Message(role="user", content="hi")])
    assert response.content == "azure answer"
    assert response.model == "gpt-4o"


def test_azure_llm_empty_messages_raises() -> None:
    llm = AzureLLM(
        LLMConfig(provider="azure", azure_endpoint="https://x.com"),
        transport=_fake_transport(_ok_response()),
    )
    with pytest.raises(ValueError, match="messages must not be empty"):
        llm.chat([])


# ---------------------------------------------------------------------------
# DeepSeek provider
# ---------------------------------------------------------------------------


def test_deepseek_llm_uses_deepseek_base_url() -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["url"] = url
        return _ok_response()

    llm = DeepSeekLLM(
        LLMConfig(provider="deepseek", api_key="ds-key"), transport=transport
    )
    llm.chat([Message(role="user", content="hi")])
    assert captured["url"].startswith("https://api.deepseek.com/")


def test_deepseek_llm_returns_chat_response() -> None:
    llm = DeepSeekLLM(
        LLMConfig(provider="deepseek"),
        transport=_fake_transport(_ok_response("deepseek says hi")),
    )
    response = llm.chat([Message(role="user", content="hi")])
    assert response.content == "deepseek says hi"


# ---------------------------------------------------------------------------
# Factory routing
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_factory():
    snapshot = dict(LLMFactory._registry)
    LLMFactory.reset()
    yield
    LLMFactory._registry.clear()
    LLMFactory._registry.update(snapshot)


def test_factory_routes_to_openai_llm() -> None:
    LLMFactory.register("openai", lambda c: OpenAILLM(c, transport=_fake_transport(_ok_response())))
    llm = LLMFactory.create(LLMConfig(provider="openai"))
    assert isinstance(llm, OpenAILLM)
    assert isinstance(llm, BaseLLM)


def test_factory_routes_to_azure_llm() -> None:
    LLMFactory.register(
        "azure",
        lambda c: AzureLLM(c, transport=_fake_transport(_ok_response())),
    )
    llm = LLMFactory.create(
        LLMConfig(provider="azure", azure_endpoint="https://x.azure.com")
    )
    assert isinstance(llm, AzureLLM)


def test_factory_routes_to_deepseek_llm() -> None:
    LLMFactory.register(
        "deepseek",
        lambda c: DeepSeekLLM(c, transport=_fake_transport(_ok_response())),
    )
    llm = LLMFactory.create(LLMConfig(provider="deepseek"))
    assert isinstance(llm, DeepSeekLLM)