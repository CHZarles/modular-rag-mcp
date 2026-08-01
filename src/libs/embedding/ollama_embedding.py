"""Ollama 本地 Embeddings 客户端。"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

JsonObject = dict[str, Any]


class OllamaEmbedding:
    """通过 Ollama ``/api/embed`` 批量生成文本向量。"""

    provider = "ollama"
    default_base_url = "http://localhost:11434"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.model = _required(config, "model")
        self.base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        self.timeout = float(config.get("timeout_seconds", config.get("timeout", 30)))
        raw_max_chars = config.get("max_input_chars")
        self.max_input_chars = int(raw_max_chars) if raw_max_chars is not None else None

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        """在一次请求中编码一条或多条文本，并保持输入顺序。"""
        self._validate_texts(texts)
        raw = self._post_json(
            f"{self.base_url}/api/embed",
            {"model": self.model, "input": list(texts)},
        )
        return _read_embeddings(raw, expected_count=len(texts))

    def _validate_texts(self, texts: list[str]) -> None:
        if not isinstance(texts, list) or not texts:
            raise ValueError("ollama input error: texts must be a non-empty list")
        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text:
                raise ValueError(f"ollama input error: texts[{index}] must be non-empty str")
            if self.max_input_chars is not None and len(text) > self.max_input_chars:
                raise ValueError(f"ollama input error: texts[{index}] exceeds max_input_chars")

    def _post_json(self, url: str, payload: Mapping[str, Any]) -> JsonObject:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"ollama HTTPError: {exc.code} {detail}") from exc
        except OSError as exc:
            raise RuntimeError(f"ollama {type(exc).__name__}: {exc}") from exc

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ollama response error: invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("ollama response error: expected JSON object")
        return data


def _required(config: Mapping[str, Any], key: str) -> str:
    value = str(config.get(key, "")).strip()
    if not value:
        raise ValueError(f"ollama configuration error: missing {key}")
    return value


def _read_embeddings(raw: Mapping[str, Any], expected_count: int) -> list[list[float]]:
    rows = raw.get("embeddings")
    if not isinstance(rows, list):
        raise RuntimeError("ollama response error: missing embeddings")
    if len(rows) != expected_count:
        raise RuntimeError("ollama response error: embedding count does not match input")

    vectors: list[list[float]] = []
    dimension: int | None = None
    for row in rows:
        if not isinstance(row, list) or not row:
            raise RuntimeError("ollama response error: embedding must be a non-empty list")
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            for value in row
        ):
            raise RuntimeError("ollama response error: embedding must contain finite numbers")

        vector = [float(value) for value in row]
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            raise RuntimeError("ollama response error: embeddings must have the same dimension")
        vectors.append(vector)
    return vectors


__all__ = ["OllamaEmbedding"]
