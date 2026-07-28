"""Azure OpenAI Embeddings 客户端。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from src.libs.embedding.openai_embedding import JsonObject, OpenAIEmbedding


class AzureOpenAIEmbedding(OpenAIEmbedding):
    """Azure OpenAI 部署版 Embeddings 客户端。"""

    provider = "azure"

    def __init__(self, config: Mapping[str, Any]) -> None:
        config_with_azure_names = dict(config)
        endpoint = config.get("endpoint") or config.get("base_url")
        if endpoint is not None:
            config_with_azure_names["base_url"] = endpoint
        deployment = config.get("deployment_name") or config.get("model")
        if deployment is not None:
            config_with_azure_names["model"] = deployment
        super().__init__(config_with_azure_names, provider=self.provider)
        self.api_version = str(config.get("api_version", "2024-02-15-preview")).strip()
        if not self.api_version:
            raise ValueError("azure configuration error: missing api_version")

    def _payload(self, texts: Sequence[str]) -> JsonObject:
        return {"input": list(texts)}

    def _embeddings_url(self) -> str:
        deployment = quote(self.model, safe="")
        return (
            f"{self.base_url}/openai/deployments/{deployment}/embeddings"
            f"?api-version={quote(self.api_version, safe='')}"
        )

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}


__all__ = ["AzureOpenAIEmbedding"]
