"""Tests for B7.4: Ollama Embedding."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.settings import EmbeddingConfig
from src.libs.embedding._http import EmbeddingHTTPError
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.embedding.ollama_embedding import OllamaEmbedding


def _ok(embedding: list[float]) -> dict[str, Any]:
    return {"embedding": embedding}


def test_ollama_embedding_returns_vectors() -> None:
    calls = []

    def transport(url, body, headers):
        calls.append(body)
        return _ok([0.1, 0.2, 0.3])

    emb = OllamaEmbedding(
        EmbeddingConfig(provider="ollama", model="nomic-embed-text"),
        transport=transport,
    )
    out = emb.embed(["hello", "world"])
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert len(calls) == 2


def test_ollama_embedding_empty_list_returns_empty() -> None:
    emb = OllamaEmbedding(
        EmbeddingConfig(provider="ollama"), transport=lambda u, b, h: _ok([0.0])
    )
    assert emb.embed([]) == []


def test_ollama_embedding_rejects_blank_text() -> None:
    emb = OllamaEmbedding(
        EmbeddingConfig(provider="ollama"), transport=lambda u, b, h: _ok([0.0])
    )
    with pytest.raises(ValueError, match="non-empty string"):
        emb.embed([""])


def test_ollama_embedding_malformed_response_raises() -> None:
    def transport(url, body, headers):
        return {"wrong": "shape"}

    emb = OllamaEmbedding(EmbeddingConfig(provider="ollama"), transport=transport)
    with pytest.raises(EmbeddingHTTPError, match="malformed response"):
        emb.embed(["x"])


def test_ollama_embedding_uses_localhost_by_default() -> None:
    captured: dict[str, Any] = {}

    def transport(url, body, headers):
        captured["url"] = url
        return _ok([0.0])

    OllamaEmbedding(
        EmbeddingConfig(provider="ollama"), transport=transport
    ).embed(["x"])
    assert captured["url"].startswith("http://localhost:11434/api/embeddings")


@pytest.fixture(autouse=True)
def _isolate_factory():
    snapshot = dict(EmbeddingFactory._registry)
    EmbeddingFactory.reset()
    yield
    EmbeddingFactory._registry.clear()
    EmbeddingFactory._registry.update(snapshot)


def test_factory_routes_ollama() -> None:
    EmbeddingFactory.register(
        "ollama",
        lambda c: OllamaEmbedding(c, transport=lambda u, b, h: _ok([0.0])),
    )
    e = EmbeddingFactory.create(EmbeddingConfig(provider="ollama"))
    assert isinstance(e, OllamaEmbedding)