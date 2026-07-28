"""Ollama 本地 Chat 客户端。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.libs.llm.openai_llm import JsonObject, _required, _serialize_messages
from src.ports.llm import ChatResponse, Message


class OllamaLLM:
    """通过 Ollama HTTP API 调用本地文本模型。"""

    provider = "ollama"
    default_base_url = "http://localhost:11434"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.model = _required(config, "model", self.provider)
        self.base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        self.timeout = float(config.get("timeout_seconds", config.get("timeout", 30)))

    def chat(
        self,
        messages: list[Message],
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """发送 Ollama chat 请求并返回统一响应对象。"""
        payload = {
            "model": self.model,
            "messages": _serialize_messages(messages, self.provider),
            "stream": False,
            **dict(kwargs),
        }
        raw = self._post_json(f"{self.base_url}/api/chat", payload)
        content = _read_ollama_content(raw)
        return ChatResponse(
            content=content,
            model=str(raw.get("model") or self.model),
            usage=_read_usage(raw),
            raw_response=raw,
        )

    def _post_json(self, url: str, payload: Mapping[str, Any]) -> JsonObject:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"ollama HTTPError: {exc.code} {detail}") from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise RuntimeError(f"ollama {type(exc).__name__}: {exc}") from exc

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ollama response error: invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("ollama response error: expected JSON object")
        return data


def _read_ollama_content(raw: Mapping[str, Any]) -> str:
    message = raw.get("message")
    if not isinstance(message, Mapping):
        raise RuntimeError("ollama response error: missing message")
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("ollama response error: message.content must be a string")
    return content


def _read_usage(raw: Mapping[str, Any]) -> dict[str, int] | None:
    usage: dict[str, int] = {}
    if isinstance(raw.get("prompt_eval_count"), int):
        usage["prompt_tokens"] = raw["prompt_eval_count"]
    if isinstance(raw.get("eval_count"), int):
        usage["completion_tokens"] = raw["eval_count"]
    if usage:
        usage["total_tokens"] = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    return usage or None


__all__ = ["OllamaLLM"]
