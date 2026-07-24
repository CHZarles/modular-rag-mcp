"""Ollama Embeddings provider — local HTTP backend."""

from __future__ import annotations

from typing import Any

from src.core.settings import EmbeddingConfig
from src.libs.embedding._http import EmbeddingHTTPError, Transport, post_json

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "nomic-embed-text"


class OllamaEmbedding:
    """Embeddings via Ollama's ``/api/embeddings`` endpoint. ``BaseEmbedding`` impl."""

    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        base_url: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.config = config
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._transport = transport

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        if texts is None:
            raise ValueError("ollama: texts must not be None")
        if any(not isinstance(t, str) or not t for t in texts):
            raise ValueError("ollama: every text must be a non-empty string")
        if not texts:
            return []

        model = self.config.model or _DEFAULT_MODEL
        # Ollama's API takes one prompt per request; call once per text.
        results: list[list[float]] = []
        for text in texts:
            payload = {"model": model, "prompt": text}
            headers = {"Content-Type": "application/json"}
            try:
                resp = post_json(
                    f"{self._base_url}/api/embeddings",
                    payload,
                    headers,
                    transport=self._transport,
                )
            except EmbeddingHTTPError as exc:
                raise EmbeddingHTTPError(f"ollama embed failed: {exc}") from exc
            try:
                results.append(resp["embedding"])
            except (KeyError, TypeError) as exc:
                raise EmbeddingHTTPError(
                    f"ollama: malformed response: {exc}"
                ) from exc
        return results