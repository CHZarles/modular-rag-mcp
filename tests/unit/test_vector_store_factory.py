"""Tests for B4: vector-store factory routing."""

from __future__ import annotations

import pytest

from src.core.settings import VectorStoreConfig
from src.libs.vector_store.vector_store_factory import (
    VectorStoreFactory,
    VectorStoreFactoryError,
)
from src.ports.ingestion import BaseVectorStore, ChunkRecord, SearchHit


class FakeVectorStore(BaseVectorStore):
    """Minimal in-test vector store backed by an in-memory list."""

    instances: list["FakeVectorStore"] = []

    def __init__(self, config: VectorStoreConfig) -> None:
        self.config = config
        self.records: list[ChunkRecord] = []
        FakeVectorStore.instances.append(self)

    def upsert(self, records, trace=None):  # type: ignore[override]
        self.records = list(records)

    def query(self, vector, top_k, filters=None, trace=None):  # type: ignore[override]
        return [
            SearchHit(id="a", text="hit", metadata={}, score=0.9, score_kind="similarity")
        ][:top_k]

    def get_by_ids(self, ids):  # type: ignore[override]
        return []

    def delete_by_metadata(self, filters):  # type: ignore[override]
        return 0


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = dict(VectorStoreFactory._registry)
    VectorStoreFactory.reset()
    yield
    VectorStoreFactory._registry.clear()
    VectorStoreFactory._registry.update(snapshot)


def test_register_and_create_routes_by_backend() -> None:
    VectorStoreFactory.register("fake", lambda c: FakeVectorStore(c))
    config = VectorStoreConfig(backend="fake", persist_path="/tmp/fake")

    store = VectorStoreFactory.create(config)

    assert isinstance(store, FakeVectorStore)
    assert store.config.persist_path == "/tmp/fake"


def test_factory_returns_base_vector_store_instance() -> None:
    VectorStoreFactory.register("fake", lambda c: FakeVectorStore(c))
    store = VectorStoreFactory.create(VectorStoreConfig(backend="fake"))
    assert isinstance(store, BaseVectorStore)


def test_factory_accepts_canonical_backend_names() -> None:
    VectorStoreFactory.reset()
    for name in ("chroma", "qdrant", "pinecone"):
        VectorStoreFactory.register(name, lambda c: FakeVectorStore(c))
    assert set(VectorStoreFactory.registered_backends()) == {"chroma", "qdrant", "pinecone"}


def test_create_unknown_backend_raises_with_registered_hint() -> None:
    with pytest.raises(VectorStoreFactoryError) as ei:
        VectorStoreFactory.create(VectorStoreConfig(backend="mystery"))
    msg = str(ei.value)
    assert "mystery" in msg
    assert "registered=" in msg


def test_register_rejects_empty_backend_name() -> None:
    VectorStoreFactory.reset()
    with pytest.raises(VectorStoreFactoryError, match="non-empty"):
        VectorStoreFactory.register("", lambda c: FakeVectorStore(c))


def test_register_rejects_non_callable_builder() -> None:
    VectorStoreFactory.reset()
    with pytest.raises(VectorStoreFactoryError, match="callable"):
        VectorStoreFactory.register("oops", 42)  # type: ignore[arg-type]


def test_register_overwrites_existing_backend() -> None:
    VectorStoreFactory.register("fake", lambda c: FakeVectorStore(c))
    VectorStoreFactory.register("fake", lambda c: FakeVectorStore(c))
    assert "fake" in VectorStoreFactory.registered_backends()


def test_reset_clears_registry() -> None:
    VectorStoreFactory.register("fake", lambda c: FakeVectorStore(c))
    VectorStoreFactory.reset()
    assert VectorStoreFactory.registered_backends() == []
    with pytest.raises(VectorStoreFactoryError):
        VectorStoreFactory.create(VectorStoreConfig(backend="fake"))