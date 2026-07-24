"""Tests for B7.3: OpenAI / Azure Embedding (mock HTTP)."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.settings import EmbeddingConfig
from src.libs.embedding._http import EmbeddingHTTPError
from src.libs.embedding.azure_embedding import AzureEmbedding
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.embedding.openai_embedding import OpenAIEmbedding


def _ok_response(vectors: list[list[float]] | None = None) -> dict[str, Any]:
    vs = vectors or [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    return {
        "object": "list",
        "data": [{"index": i, "embedding": v} for i, v in enumerate(vs)],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


def test_openai_embedding_returns_vectors() -> None:
    emb = OpenAIEmbedding(
        EmbeddingConfig(provider="openai", api_key="sk-x"),
        transport=lambda u, b, h: _ok_response(),
    )
    out = emb.embed(["hello", "world"])
    assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


def test_openai_embedding_sends_bearer_auth() -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["headers"] = headers
        return _ok_response()

    emb = OpenAIEmbedding(
        EmbeddingConfig(provider="openai", api_key="sk-abc"),
        transport=transport,
    )
    emb.embed(["x"])
    assert captured["headers"]["Authorization"] == "Bearer sk-abc"


def test_openai_embedding_empty_list_returns_empty() -> None:
    emb = OpenAIEmbedding(
        EmbeddingConfig(provider="openai"), transport=lambda u, b, h: _ok_response()
    )
    assert emb.embed([]) == []


def test_openai_embedding_rejects_blank_text() -> None:
    emb = OpenAIEmbedding(
        EmbeddingConfig(provider="openai"), transport=lambda u, b, h: _ok_response()
    )
    with pytest.raises(ValueError, match="non-empty string"):
        emb.embed([""])


def test_openai_embedding_rejects_overlong_input() -> None:
    emb = OpenAIEmbedding(
        EmbeddingConfig(provider="openai", model="m"),
        transport=lambda u, b, h: _ok_response(),
        max_input_len=10,
    )
    with pytest.raises(ValueError, match="max_input_len"):
        emb.embed(["x" * 11])


def test_openai_embedding_malformed_response_raises() -> None:
    emb = OpenAIEmbedding(
        EmbeddingConfig(provider="openai"),
        transport=lambda u, b, h: {"data": [{"wrong_key": 1}]},
    )
    with pytest.raises(EmbeddingHTTPError, match="malformed response"):
        emb.embed(["x"])


def test_openai_embedding_transport_failure_wraps_with_provider() -> None:
    def transport(url, body, headers):
        raise EmbeddingHTTPError("network error")

    emb = OpenAIEmbedding(EmbeddingConfig(provider="openai"), transport=transport)
    with pytest.raises(EmbeddingHTTPError, match="openai embed failed"):
        emb.embed(["x"])


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------


def test_azure_embedding_requires_endpoint() -> None:
    with pytest.raises(ValueError, match="azure_endpoint"):
        AzureEmbedding(EmbeddingConfig(provider="azure"))


def test_azure_embedding_returns_vectors() -> None:
    emb = AzureEmbedding(
        EmbeddingConfig(
            provider="azure",
            model="text-embedding-ada-002",
            azure_endpoint="https://example.azure.com",
            api_key="k",
        ),
        transport=lambda u, b, h: _ok_response([[0.7, 0.8]]),
    )
    assert emb.embed(["x"]) == [[0.7, 0.8]]


def test_azure_embedding_uses_api_key_header_and_deployment_in_url() -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["url"] = url
        captured["headers"] = headers
        return _ok_response()

    emb = AzureEmbedding(
        EmbeddingConfig(
            provider="azure",
            model="my-deployment",
            azure_endpoint="https://example.azure.com",
            api_key="azure-key",
        ),
        transport=transport,
    )
    emb.embed(["x"])
    assert "/openai/deployments/my-deployment/embeddings" in captured["url"]
    assert "api-version=" in captured["url"]
    assert captured["headers"]["api-key"] == "azure-key"


# ---------------------------------------------------------------------------
# Factory routing
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_factory():
    snapshot = dict(EmbeddingFactory._registry)
    EmbeddingFactory.reset()
    yield
    EmbeddingFactory._registry.clear()
    EmbeddingFactory._registry.update(snapshot)


def test_factory_routes_openai() -> None:
    EmbeddingFactory.register(
        "openai",
        lambda c: OpenAIEmbedding(c, transport=lambda u, b, h: _ok_response()),
    )
    e = EmbeddingFactory.create(EmbeddingConfig(provider="openai"))
    assert isinstance(e, OpenAIEmbedding)


def test_factory_routes_azure() -> None:
    EmbeddingFactory.register(
        "azure",
        lambda c: AzureEmbedding(c, transport=lambda u, b, h: _ok_response()),
    )
    e = EmbeddingFactory.create(
        EmbeddingConfig(provider="azure", azure_endpoint="https://x.com")
    )
    assert isinstance(e, AzureEmbedding)