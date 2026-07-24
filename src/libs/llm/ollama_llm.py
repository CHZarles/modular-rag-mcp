"""Ollama LLM provider — local HTTP backend.

Ollama exposes a Chat Completions-compatible endpoint at ``/api/chat``
with a slightly different payload shape than OpenAI: messages have a
``role``/``content`` pair but no token/usage stats are returned.

For robustness we add a connection-failure error path that does **not**
echo the endpoint URL back to the caller (avoid leaking private
``localhost:11434`` style addresses).
"""

from __future__ import annotations

from typing import Any

from src.core.settings import LLMConfig
from src.libs.llm._http import LLMHTTPError, Transport, post_json
from src.ports.llm import BaseLLM, ChatResponse, Message

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3"


def _messages_to_ollama(messages: list[Message]) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


class OllamaLLM(BaseLLM):
    """Ollama Chat Completions client."""

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
            raise ValueError("ollama: messages must not be empty")
        model = self.config.model or _DEFAULT_MODEL
        url = f"{self._base_url}/api/chat"
        payload = {"model": model, "messages": _messages_to_ollama(messages), **kwargs}
        headers = {"Content-Type": "application/json"}

        try:
            response = post_json(url, payload, headers, transport=self._transport)
        except LLMHTTPError as exc:
            # Don't leak base_url / api_key in the error message.
            raise LLMHTTPError(
                f"ollama chat failed: {type(exc).__name__}"
            ) from exc

        try:
            content = response["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMHTTPError(f"ollama: malformed response: {exc}") from exc

        return ChatResponse(
            content=content,
            model=response.get("model", model),
            usage=None,
            raw_response=response,
        )