"""Tests for B2: embedding factory routing + stable fake vectors."""

from __future__ import annotations

import hashlib

import pytest

from src.core.settings import EmbeddingConfig
from src.libs.embedding.embedding_factory import EmbeddingFactory, EmbeddingFactoryError
from src.ports.ingestion import BaseEmbedding

VECTOR_DIM = 8


def _stable_vector(text: str, dim: int = VECTOR_DIM) -> list[float]:
    """Deterministic fake embedding: hash → normalised floats."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [b / 255.0 for b in digest[:dim]]
    # Pad if digest shorter than dim
    while len(raw) < dim:
        raw.append(0.0)
    return raw[:dim]


class FakeEmbedding(BaseEmbedding):
    """Stable, deterministic in-test embedding."""

    instances: list["FakeEmbedding"] = []

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self.calls: list[list[str]] = []
        FakeEmbedding.instances.append(self)

    def embed(self, texts, trace=None):  # type: ignore[override]
        self.calls.append(list(texts))
        return [_stable_vector(t) for t in texts]


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = dict(EmbeddingFactory._registry)
    EmbeddingFactory.reset()
    yield
    EmbeddingFactory._registry.clear()
    EmbeddingFactory._registry.update(snapshot)


def test_register_and_create_routes_by_provider() -> None:
    EmbeddingFactory.register("fake", lambda c: FakeEmbedding(c))
    config = EmbeddingConfig(provider="fake", model="text-embedding-fake")

    embedding = EmbeddingFactory.create(config)

    assert isinstance(embedding, FakeEmbedding)
    assert embedding.config.model == "text-embedding-fake"


def test_factory_returns_base_embedding_instance() -> None:
    EmbeddingFactory.register("fake", lambda c: FakeEmbedding(c))
    embedding = EmbeddingFactory.create(EmbeddingConfig(provider="fake"))
    assert isinstance(embedding, BaseEmbedding)


def test_fake_embedding_returns_stable_vectors() -> None:
    """Same text → same vector across calls (idempotent, deterministic)."""
    EmbeddingFactory.register("fake", lambda c: FakeEmbedding(c))
    embedding = EmbeddingFactory.create(EmbeddingConfig(provider="fake"))

    first = embedding.embed(["hello", "world"])
    second = embedding.embed(["hello", "world"])

    assert first == second
    assert len(first) == 2
    assert all(len(v) == VECTOR_DIM for v in first)


def test_fake_embedding_distinguishes_texts() -> None:
    """Different texts → different vectors (sanity for the fake)."""
    EmbeddingFactory.register("fake", lambda c: FakeEmbedding(c))
    embedding = EmbeddingFactory.create(EmbeddingConfig(provider="fake"))

    vectors = embedding.embed(["alpha", "beta"])
    assert vectors[0] != vectors[1]


def test_create_unknown_provider_raises_with_registered_hint() -> None:
    with pytest.raises(EmbeddingFactoryError) as ei:
        EmbeddingFactory.create(EmbeddingConfig(provider="nope"))
    msg = str(ei.value)
    assert "nope" in msg
    assert "registered=" in msg


def test_register_accepts_canonical_provider_names() -> None:
    EmbeddingFactory.reset()
    for name in ("openai", "azure", "ollama"):
        EmbeddingFactory.register(name, lambda c: FakeEmbedding(c))
    assert set(EmbeddingFactory.registered_providers()) == {"openai", "azure", "ollama"}


def test_register_rejects_empty_provider_name() -> None:
    EmbeddingFactory.reset()
    with pytest.raises(EmbeddingFactoryError, match="non-empty"):
        EmbeddingFactory.register("", lambda c: FakeEmbedding(c))


def test_register_rejects_non_callable_builder() -> None:
    EmbeddingFactory.reset()
    with pytest.raises(EmbeddingFactoryError, match="callable"):
        EmbeddingFactory.register("oops", 42)  # type: ignore[arg-type]


def test_embed_empty_list_returns_empty_list() -> None:
    EmbeddingFactory.register("fake", lambda c: FakeEmbedding(c))
    embedding = EmbeddingFactory.create(EmbeddingConfig(provider="fake"))
    assert embedding.embed([]) == []