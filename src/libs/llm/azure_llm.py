"""Azure OpenAI LLM provider.

Speaks the Azure OpenAI Chat Completions REST API. Azure differs from
OpenAI in two ways:

- The endpoint comes from ``settings.llm.azure_endpoint`` and embeds
  the deployment name in the URL path.
- Authentication uses the ``api-key`` header instead of a Bearer token.
"""

from __future__ import annotations

from typing import Any

from src.core.settings import LLMConfig
from src.libs.llm._http import LLMHTTPError, Transport, post_json
from src.libs.llm.openai_llm import _messages_to_openai
from src.ports.llm import BaseLLM, ChatResponse, Message


class AzureLLM(BaseLLM):
    """Azure OpenAI Chat Completions client."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: Transport | None = None,
        deployment: str | None = None,
        api_version: str = "2024-02-01",
    ) -> None:
        self.config = config
        if not config.azure_endpoint:
            raise ValueError(
                "azure provider requires settings.llm.azure_endpoint"
            )
        self._endpoint = config.azure_endpoint.rstrip("/")
        self._deployment = deployment or config.model or "gpt-4o"
        self._api_version = api_version
        self._transport = transport

    def chat(
        self,
        messages: list[Message],
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if not messages:
            raise ValueError("azure: messages must not be empty")
        url = (
            f"{self._endpoint}/openai/deployments/{self._deployment}"
            f"/chat/completions?api-version={self._api_version}"
        )
        payload = {
            "messages": _messages_to_openai(messages),
            **kwargs,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["api-key"] = self.config.api_key

        try:
            response = post_json(url, payload, headers, transport=self._transport)
        except LLMHTTPError as exc:
            raise LLMHTTPError(f"azure chat failed: {exc}") from exc

        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMHTTPError(f"azure: malformed response: {exc}") from exc

        return ChatResponse(
            content=content,
            model=self._deployment,
            usage=response.get("usage"),
            raw_response=response,
        )