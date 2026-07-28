from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.settings import Settings
from libs.embedding import EmbeddingFactory, create_embedding


class FakeEmbedding:
    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def embed(self, texts: list[str], trace: object | None = None) -> list[list[float]]:
        return [[float(len(text))] * self.dimension for text in texts]


@pytest.fixture(autouse=True)
def cleanup_fake_provider() -> Iterator[None]:
    EmbeddingFactory.unregister("fake")
    yield
    EmbeddingFactory.unregister("fake")


def test_embedding_factory_routes_by_provider() -> None:
    EmbeddingFactory.register("fake", lambda config: FakeEmbedding(int(config["dimension"])))
    settings = Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "fake", "dimension": 3},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": True},
    )

    embedding = EmbeddingFactory.create(settings)

    assert embedding.embed(["a", "abcd"]) == [[1.0, 1.0, 1.0], [4.0, 4.0, 4.0]]


def test_create_embedding_accepts_embedding_config_mapping() -> None:
    EmbeddingFactory.register("fake", lambda config: FakeEmbedding(int(config["dimension"])))

    embedding = create_embedding({"provider": "fake", "dimension": 2})

    assert embedding.embed(["abc"]) == [[3.0, 3.0]]


def test_embedding_factory_names_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported Embedding provider: missing"):
        EmbeddingFactory.create({"embedding": {"provider": "missing"}})


def test_embedding_factory_requires_provider() -> None:
    with pytest.raises(ValueError, match=r"embedding\.provider"):
        EmbeddingFactory.create({"embedding": {}})
