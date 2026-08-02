"""MiniMax Embeddings 客户端。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

JsonObject = dict[str, Any]


class MiniMaxEmbedding:
    """通过 MiniMax 原生 Embeddings API 生成文档和查询向量。"""

    provider = "minimax"
    default_base_url = "https://api.minimax.io/v1"

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.model = _required(config, "model")
        self.api_key = _required(config, "api_key")
        self.base_url = str(config.get("base_url") or self.default_base_url).rstrip("/")
        self.group_id = str(config.get("group_id") or "").strip() or None
        self.timeout = float(config.get("timeout_seconds", config.get("timeout", 30)))
        self.document_type = str(config.get("document_type") or "db")
        self.query_type = str(config.get("query_type") or "query")
        raw_max_chars = config.get("max_input_chars")
        self.max_input_chars = int(raw_max_chars) if raw_max_chars is not None else None

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        """按 MiniMax 的 ``db`` 类型批量编码待索引文本。"""
        return self._embed(texts, self.document_type)

    def embed_query(self, text: str, trace: Any | None = None) -> list[float]:
        """按 MiniMax 的 ``query`` 类型编码检索问题。"""
        return self._embed([text], self.query_type)[0]

    def _embed(self, texts: list[str], embedding_type: str) -> list[list[float]]:
        self._validate_texts(texts)
        payload = {"model": self.model, "type": embedding_type, "texts": texts}
        raw = self._post_json(self._embeddings_url(), payload)
        return _read_vectors(raw, len(texts))

    def _validate_texts(self, texts: list[str]) -> None:
        if not isinstance(texts, list) or not texts:
            raise ValueError("minimax input error: texts must be a non-empty list")
        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text:
                raise ValueError(f"minimax input error: texts[{index}] must be non-empty str")
            if self.max_input_chars is not None and len(text) > self.max_input_chars:
                raise ValueError(f"minimax input error: texts[{index}] exceeds max_input_chars")

    def _embeddings_url(self) -> str:
        url = f"{self.base_url}/embeddings"
        if self.group_id is not None:
            return f"{url}?{urlencode({'GroupId': self.group_id})}"
        return url

    def _post_json(self, url: str, payload: Mapping[str, Any]) -> JsonObject:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"minimax HTTPError: {exc.code} {detail}") from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise RuntimeError(f"minimax {type(exc).__name__}: {exc}") from exc

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("minimax response error: invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("minimax response error: expected JSON object")
        return data


def _required(config: Mapping[str, Any], key: str) -> str:
    value = str(config.get(key, "")).strip()
    if not value:
        raise ValueError(f"minimax configuration error: missing {key}")
    return value


def _read_vectors(raw: Mapping[str, Any], expected_count: int) -> list[list[float]]:
    base_response = raw.get("base_resp")
    if not isinstance(base_response, Mapping):
        raise RuntimeError("minimax response error: missing base_resp")
    if base_response.get("status_code") != 0:
        status_code = base_response.get("status_code", "unknown")
        status_message = str(base_response.get("status_msg", "request failed"))[:200]
        raise RuntimeError(f"minimax API error: {status_code} {status_message}")

    rows = raw.get("vectors")
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise RuntimeError("minimax response error: vector count does not match input")
    vectors: list[list[float]] = []
    for row in rows:
        if not isinstance(row, list) or not row:
            raise RuntimeError("minimax response error: invalid vector")
        if not all(isinstance(value, int | float) and not isinstance(value, bool) for value in row):
            raise RuntimeError("minimax response error: vector must be numeric")
        vectors.append([float(value) for value in row])
    return vectors


__all__ = ["MiniMaxEmbedding"]
