"""Azure Vision LLM implementation — supports both image path and base64 input."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from src.core.settings import VisionLLMConfig
from src.libs.llm._http import LLMHTTPError, Transport, post_json
from src.ports.llm import BaseVisionLLM, ChatResponse, ImageInput, Message

_DEFAULT_MODEL = "gpt-4o"


class AzureVisionLLM(BaseVisionLLM):
    """Azure OpenAI Vision (chat-completions with image_url content)."""

    def __init__(
        self,
        config: VisionLLMConfig,
        *,
        transport: Transport | None = None,
        api_version: str = "2024-02-01",
        deployment: str | None = None,
    ) -> None:
        if not config.azure_endpoint:
            raise ValueError(
                "azure vision provider requires settings.vision_llm.azure_endpoint"
            )
        self.config = config
        self._endpoint = config.azure_endpoint.rstrip("/")
        self._deployment = deployment or config.model or _DEFAULT_MODEL
        self._api_version = api_version
        self._transport = transport

    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: list[Message] | None = None,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if not isinstance(text, str) or not text:
            raise ValueError("azure vision: text must be a non-empty string")

        url = (
            f"{self._endpoint}/openai/deployments/{self._deployment}"
            f"/chat/completions?api-version={self._api_version}"
        )
        image_url = _image_to_data_url(image)
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            **kwargs,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["api-key"] = self.config.api_key

        try:
            response = post_json(url, payload, headers, transport=self._transport)
        except LLMHTTPError as exc:
            raise LLMHTTPError(f"azure vision failed: {exc}") from exc

        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMHTTPError(f"azure vision: malformed response: {exc}") from exc

        return ChatResponse(
            content=content,
            model=self._deployment,
            usage=response.get("usage"),
            raw_response=response,
        )


def _image_to_data_url(image: ImageInput) -> str:
    """Convert an ImageInput (path / bytes / base64) into a data: URL string."""
    if image.base64 is not None:
        return f"data:{image.mime_type};base64,{image.base64}"
    if image.data is not None:
        encoded = base64.b64encode(image.data).decode("ascii")
        return f"data:{image.mime_type};base64,{encoded}"
    if image.path is not None:
        data = Path(image.path).read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{image.mime_type};base64,{encoded}"
    raise ValueError("azure vision: image has no path, data, or base64")