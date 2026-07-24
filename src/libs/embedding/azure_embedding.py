"""Azure OpenAI Embeddings provider.

Azure's embedding endpoint URL embeds the deployment in the path and
adds the api-version as a query parameter, mirroring the chat shape.
The core validation logic is inherited from :class:`OpenAIEmbedding`.
"""

from __future__ import annotations

from typing import Any

from src.core.settings import EmbeddingConfig
from src.libs.embedding._http import EmbeddingHTTPError, Transport, post_json


class AzureEmbedding:
    """Embeddings via Azure OpenAI deployment. Implements ``BaseEmbedding``."""

    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        transport: Transport | None = None,
        api_version: str = "2024-02-01",
    ) -> None:
        if not config.azure_endpoint:
            raise ValueError("azure provider requires settings.embedding.azure_endpoint")
        self.config = config
        self._endpoint = config.azure_endpoint.rstrip("/")
        self._deployment = config.model or "text-embedding-ada-002"
        self._api_version = api_version
        self._transport = transport

    def embed(self, texts: list[str], trace: Any | None = None) -> list[list[float]]:
        if texts is None:
            raise ValueError("azure: texts must not be None")
        if any(not isinstance(t, str) or not t for t in texts):
            raise ValueError("azure: every text must be a non-empty string")

        url = (
            f"{self._endpoint}/openai/deployments/{self._deployment}"
            f"/embeddings?api-version={self._api_version}"
        )
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["api-key"] = self.config.api_key
        payload = {"input": texts}

        try:
            response = post_json(url, payload, headers, transport=self._transport)
        except EmbeddingHTTPError as exc:
            raise EmbeddingHTTPError(f"azure embed failed: {exc}") from exc

        try:
            return [item["embedding"] for item in response["data"]]
        except (KeyError, TypeError) as exc:
            raise EmbeddingHTTPError(f"azure: malformed response: {exc}") from exc