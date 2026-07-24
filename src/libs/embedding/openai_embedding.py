"""OpenAI Embeddings provider."""

from __future__ import annotations

from typing import Any

from src.core.settings import EmbeddingConfig
from src.libs.embedding._http import (
    EmbeddingHTTPError,
    Transport,
    post_json,
)
from src.ports.ingestion import BaseEmbedding

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "text-embedding-3-small"
_MAX_INPUT_LEN = 8192  # OpenAI's documented token-ish limit; we approximate by chars.


class OpenAIEmbedding(BaseEmbedding):
    """Embeds text using OpenAI's ``/v1/embeddings`` endpoint."""

    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        base_url: str | None = None,
        transport: Transport | None = None,
        max_input_len: int = _MAX_INPUT_LEN,
    ) -> None:
        self.config = config
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._transport = transport
        self._max_input_len = max_input_len

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        if texts is None:
            raise ValueError("openai: texts must not be None")
        if any(not isinstance(t, str) or not t for t in texts):
            raise ValueError("openai: every text must be a non-empty string")
        if any(len(t) > self._max_input_len for t in texts):
            raise ValueError(
                f"openai: input exceeds max_input_len={self._max_input_len} characters"
            )
        if not texts:
            return []

        model = self.config.model or _DEFAULT_MODEL
        payload = {"model": model, "input": texts}
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            response = post_json(
                f"{self._base_url}/embeddings",
                payload,
                headers,
                transport=self._transport,
            )
        except EmbeddingHTTPError as exc:
            raise EmbeddingHTTPError(f"openai embed failed: {exc}") from exc

        try:
            return [item["embedding"] for item in response["data"]]
        except (KeyError, TypeError) as exc:
            raise EmbeddingHTTPError(f"openai: malformed response: {exc}") from exc