"""OpenAI-compatible Chat Completions 客户端。"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.ports.llm import (
    ChatResponse,
    ImageInput,
    ImagePreprocessor,
    Message,
    preprocess_image,
)

JsonObject = dict[str, Any]


class OpenAICompatibleLLM:
    """通过 OpenAI-compatible HTTP API 调用文本及多模态模型。"""

    provider = "openai"
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, config: Mapping[str, Any], provider: str | None = None) -> None:
        self.provider = provider or self.provider
        self.model = _required(config, "model", self.provider)
        self.api_key = _required(config, "api_key", self.provider)
        self.base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        self.timeout = float(config.get("timeout_seconds", config.get("timeout", 30)))

    def chat(
        self,
        messages: list[Message],
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """发送 Chat Completions 请求并返回统一响应对象。"""
        payload = self._payload(messages, kwargs)
        raw = self._post_json(self._chat_url(), payload)
        content = _read_content(raw, self.provider)
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        return ChatResponse(
            content=content,
            model=str(raw.get("model") or payload.get("model") or self.model),
            usage=usage,
            raw_response=raw,
        )

    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: list[Message] | None = None,
        trace: Any | None = None,
        preprocessor: ImagePreprocessor | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """按 OpenAI-compatible ``image_url`` 格式发送文本和单张图片。"""
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{self.provider} input error: text must not be empty")
        image = preprocess_image(image, preprocessor)

        serialized: list[JsonObject] = (
            list(_serialize_messages(messages, self.provider)) if messages else []
        )
        serialized.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image)},
                    },
                ],
            }
        )
        payload: JsonObject = {
            "model": self.model,
            "messages": serialized,
            **dict(kwargs),
        }
        raw = self._post_json(self._chat_url(), payload)
        content = _read_content(raw, self.provider)
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        return ChatResponse(
            content=content,
            model=str(raw.get("model") or payload.get("model") or self.model),
            usage=usage,
            raw_response=raw,
        )

    def _payload(self, messages: list[Message], kwargs: Mapping[str, Any]) -> JsonObject:
        return {
            "model": self.model,
            "messages": _serialize_messages(messages, self.provider),
            **dict(kwargs),
        }

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post_json(self, url: str, payload: Mapping[str, Any]) -> JsonObject:
        body = json.dumps(payload).encode("utf-8")
        request = Request(url, data=body, headers=self._headers(), method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"{self.provider} HTTPError: {exc.code} {detail}") from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise RuntimeError(f"{self.provider} {type(exc).__name__}: {exc}") from exc

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{self.provider} response error: invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{self.provider} response error: expected JSON object")
        return data


def _required(config: Mapping[str, Any], key: str, provider: str) -> str:
    value = str(config.get(key, "")).strip()
    if not value:
        raise ValueError(f"{provider} configuration error: missing {key}")
    return value


def _serialize_messages(messages: list[Message], provider: str) -> list[dict[str, str]]:
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"{provider} input error: messages must be a non-empty list")

    serialized: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Message):
            raise ValueError(f"{provider} input error: messages[{index}] must be Message")
        serialized.append({"role": message.role, "content": message.content})
    return serialized


def _image_data_url(image: ImageInput) -> str:
    """把统一图片输入编码为 OpenAI-compatible API 接受的 Data URL。"""
    if image.path is not None:
        data = Path(image.path).read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
    elif image.data is not None:
        encoded = base64.b64encode(image.data).decode("ascii")
    else:
        encoded = str(image.base64).strip()
    return f"data:{image.mime_type};base64,{encoded}"


def _read_content(raw: Mapping[str, Any], provider: str) -> str:
    try:
        content = raw["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"{provider} response error: missing choices[0].message.content"
        ) from exc
    if not isinstance(content, str):
        raise RuntimeError(f"{provider} response error: content must be a string")
    return content


__all__ = ["OpenAICompatibleLLM"]
