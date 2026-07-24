"""B7.6 roundtrip test: upsert → query → filter → delete, with disk persistence."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.core.settings import VectorStoreConfig
from src.core.types import ChunkRecord, SearchHit
from src.libs.vector_store.chroma_store import ChromaStore


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    d = tmp_path / "chroma"
    d.mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def store(store_dir: Path) -> ChromaStore:
    return ChromaStore(VectorStoreConfig(backend="chroma", persist_path=str(store_dir)))


def _record(rid: str, text: str, vec: list[float], **meta) -> ChunkRecord:
    return ChunkRecord(id=rid, text=text, metadata=dict(meta), dense_vector=vec)


def test_upsert_then_query_returns_top_k_by_cosine(store: ChromaStore) -> None:
    store.upsert(
        [
            _record("a", "Azure OpenAI setup", [1.0, 0.0, 0.0], collection="docs"),
            _record("b", "BM25 keyword retrieval", [0.0, 1.0, 0.0], collection="docs"),
            _record("c", "Hybrid RRF fusion", [0.7, 0.7, 0.0], collection="docs"),
        ]
    )
    hits = store.query(vector=[1.0, 0.0, 0.0], top_k=2)
    assert [h.id for h in hits] == ["a", "c"]
    assert hits[0].score > hits[1].score > 0
    assert all(isinstance(h, SearchHit) for h in hits)


def test_metadata_filters_narrow_results(store: ChromaStore) -> None:
    store.upsert(
        [
            _record("a", "doc a", [1.0, 0.0], collection="docs", source="a.pdf"),
            _record("b", "doc b", [1.0, 0.0], collection="docs", source="b.pdf"),
            _record("c", "doc c", [1.0, 0.0], collection="other", source="a.pdf"),
        ]
    )
    hits = store.query(
        vector=[1.0, 0.0], top_k=10, filters={"collection": "docs", "source": "a.pdf"}
    )
    assert [h.id for h in hits] == ["a"]


def test_persistence_round_trip(store: ChromaStore, store_dir: Path) -> None:
    """Reopening the store from the same persist_path must reload records."""
    store.upsert([_record("a", "doc", [1.0, 0.0, 0.0])])

    fresh = ChromaStore(VectorStoreConfig(backend="chroma", persist_path=str(store_dir)))
    hits = fresh.query(vector=[1.0, 0.0, 0.0], top_k=1)

    assert len(hits) == 1
    assert hits[0].id == "a"
    assert hits[0].text == "doc"


def test_upsert_is_idempotent_by_id(store: ChromaStore) -> None:
    store.upsert([_record("a", "v1", [1.0, 0.0])])
    store.upsert([_record("a", "v2", [0.0, 1.0])])
    assert len(store.get_by_ids(["a"])) == 1
    assert store.get_by_ids(["a"])[0].text == "v2"


def test_get_by_ids_returns_only_existing(store: ChromaStore) -> None:
    store.upsert([_record("a", "x", [1.0, 0.0]), _record("b", "y", [0.0, 1.0])])
    records = store.get_by_ids(["a", "missing", "b"])
    assert [r.id for r in records] == ["a", "b"]


def test_delete_by_metadata(store: ChromaStore) -> None:
    store.upsert(
        [
            _record("a", "x", [1.0, 0.0], source="a.pdf"),
            _record("b", "y", [0.0, 1.0], source="a.pdf"),
            _record("c", "z", [1.0, 0.0], source="other.pdf"),
        ]
    )
    deleted = store.delete_by_metadata({"source": "a.pdf"})
    assert deleted == 2
    assert store.get_by_ids(["a", "b", "c"]) == store.get_by_ids(["c"])


def test_query_with_no_records_returns_empty(store: ChromaStore) -> None:
    assert store.query(vector=[1.0, 0.0], top_k=5) == []


def test_factory_routes_chroma_backend(store_dir: Path) -> None:
    from src.libs.vector_store.vector_store_factory import VectorStoreFactory

    snapshot = dict(VectorStoreFactory._registry)
    VectorStoreFactory.reset()
    try:
        VectorStoreFactory.register(
            "chroma",
            lambda c: ChromaStore(
                VectorStoreConfig(backend="chroma", persist_path=str(store_dir))
            ),
        )
        backend = VectorStoreFactory.create(
            VectorStoreConfig(backend="chroma", persist_path=str(store_dir))
        )
        assert isinstance(backend, ChromaStore)
    finally:
        VectorStoreFactory._registry.clear()
        VectorStoreFactory._registry.update(snapshot)