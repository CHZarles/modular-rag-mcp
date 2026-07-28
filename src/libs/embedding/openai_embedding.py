"""OpenAI-compatible Embeddings 客户端。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

JsonObject = dict[str, Any]


class OpenAIEmbedding:
    """通过 OpenAI-compatible HTTP API 批量生成文本向量。"""

    provider = "openai"
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, config: Mapping[str, Any], provider: str | None = None) -> None:
        self.provider = provider or self.provider
        self.model = _required(config, "model", self.provider)
        self.api_key = _required(config, "api_key", self.provider)
        self.base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        self.timeout = float(config.get("timeout_seconds", config.get("timeout", 30)))
        raw_max_chars = config.get("max_input_chars")
        self.max_input_chars = int(raw_max_chars) if raw_max_chars is not None else None

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        """发送 Embeddings 请求并按输入顺序返回向量。"""
        self._validate_texts(texts)
        raw = self._post_json(self._embeddings_url(), self._payload(texts))
        return _read_embeddings(raw, self.provider)

    def _validate_texts(self, texts: list[str]) -> None:
        if not isinstance(texts, list) or not texts:
            raise ValueError(f"{self.provider} input error: texts must be a non-empty list")
        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text:
                raise ValueError(f"{self.provider} input error: texts[{index}] must be non-empty str")
            if self.max_input_chars is not None and len(text) > self.max_input_chars:
                raise ValueError(
                    f"{self.provider} input error: texts[{index}] exceeds max_input_chars"
                )

    def _payload(self, texts: Sequence[str]) -> JsonObject:
        return {"model": self.model, "input": list(texts)}

    def _embeddings_url(self) -> str:
        return f"{self.base_url}/embeddings"

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


def _read_embeddings(raw: Mapping[str, Any], provider: str) -> list[list[float]]:
    rows = raw.get("data")
    if not isinstance(rows, list):
        raise RuntimeError(f"{provider} response error: missing data")

    indexed_rows = sorted(
        enumerate(rows),
        key=lambda item: item[1].get("index", item[0]) if isinstance(item[1], dict) else item[0],
    )
    vectors: list[list[float]] = []
    for _, row in indexed_rows:
        if not isinstance(row, dict) or not isinstance(row.get("embedding"), list):
            raise RuntimeError(f"{provider} response error: invalid embedding row")
        vector = row["embedding"]
        if not all(isinstance(value, int | float) for value in vector):
            raise RuntimeError(f"{provider} response error: embedding must be numeric")
        vectors.append([float(value) for value in vector])
    return vectors


__all__ = ["OpenAIEmbedding"]
