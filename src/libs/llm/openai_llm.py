"""OpenAI LLM provider (also a template for DeepSeek and other compatible APIs).

Speaks the OpenAI Chat Completions API. The transport is injectable so
unit tests can substitute a fake response without hitting the network.
"""

from __future__ import annotations

from typing import Any, Callable

from src.core.settings import LLMConfig
from src.libs.llm._http import LLMHTTPError, Transport, post_json
from src.ports.llm import BaseLLM, ChatResponse, Message

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"


def _messages_to_openai(messages: list[Message]) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


class OpenAILLM(BaseLLM):
    """Chat Completions-compatible LLM (OpenAI, DeepSeek, etc.)."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        base_url: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.config = config
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._transport = transport

    def chat(
        self,
        messages: list[Message],
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if not messages:
            raise ValueError(f"{self.config.provider}: messages must not be empty")
        for m in messages:
            if m.role not in {"system", "user", "assistant"}:
                raise ValueError(
                    f"{self.config.provider}: invalid role {m.role!r}"
                )
            if not isinstance(m.content, str) or not m.content:
                raise ValueError(
                    f"{self.config.provider}: message content must be a non-empty string"
                )

        model = self.config.model or _DEFAULT_MODEL
        payload = {
            "model": model,
            "messages": _messages_to_openai(messages),
            **kwargs,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            response = post_json(
                f"{self._base_url}/chat/completions",
                payload,
                headers,
                transport=self._transport,
            )
        except LLMHTTPError as exc:
            raise LLMHTTPError(
                f"{self.config.provider} chat failed: {exc}"
            ) from exc

        try:
            choice = response["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMHTTPError(
                f"{self.config.provider}: malformed response: {exc}"
            ) from exc

        usage = response.get("usage") if isinstance(response, dict) else None
        return ChatResponse(
            content=content,
            model=response.get("model", model),
            usage=usage,
            raw_response=response,
        )


def build_openai_llm(config: LLMConfig, transport: Transport | None = None) -> BaseLLM:
    """Factory builder used by ``LLMFactory.register``."""
    return OpenAILLM(config, transport=transport)


def register(transport: Transport | None = None) -> Callable[[LLMConfig], BaseLLM]:
    """Return a builder closure that can be handed to ``LLMFactory.register``."""
    return lambda config: build_openai_llm(config, transport=transport)