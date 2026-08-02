from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.settings import Settings
from core.types import ChunkRecord, JsonDict, SearchHit
from libs.vector_store import BaseVectorStore, VectorStoreFactory, create_vector_store


class FakeVectorStore:
    def __init__(self) -> None:
        self.records: dict[str, ChunkRecord] = {}

    def upsert(self, records: list[ChunkRecord], trace: object | None = None) -> None:
        for record in records:
            self.records[record.id] = record

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: JsonDict | None = None,
        trace: object | None = None,
    ) -> list[SearchHit]:
        records = list(self.records.values())
        if filters:
            records = [
                record
                for record in records
                if all(record.metadata.get(key) == value for key, value in filters.items())
            ]
        return [
            SearchHit(
                id=record.id,
                text=record.text,
                metadata=record.metadata,
                score=float(len(vector)),
                score_kind="similarity",
            )
            for record in records[:top_k]
        ]

    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]:
        return [self.records[record_id] for record_id in ids if record_id in self.records]

    def delete_by_metadata(self, filters: JsonDict) -> int:
        if not filters:
            raise ValueError("vector store delete filters must not be empty")
        matched = [
            record_id
            for record_id, record in self.records.items()
            if all(record.metadata.get(key) == value for key, value in filters.items())
        ]
        for record_id in matched:
            del self.records[record_id]
        return len(matched)


@pytest.fixture(autouse=True)
def cleanup_fake_backend() -> Iterator[None]:
    VectorStoreFactory.unregister("fake")
    yield
    VectorStoreFactory.unregister("fake")


def test_vector_store_factory_routes_by_backend() -> None:
    VectorStoreFactory.register("fake", lambda config: FakeVectorStore())
    settings = Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "fake"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": True},
    )

    store = VectorStoreFactory.create(settings)

    assert isinstance(store, BaseVectorStore)


def test_vector_store_contract_shapes() -> None:
    store = FakeVectorStore()
    records = [
        ChunkRecord(id="chunk-1", text="alpha", metadata={"collection": "docs"}),
        ChunkRecord(id="chunk-2", text="beta", metadata={"collection": "notes"}),
    ]

    store.upsert(records)
    hits = store.query([0.1, 0.2], top_k=1, filters={"collection": "docs"})

    assert hits == [
        SearchHit(
            id="chunk-1",
            text="alpha",
            metadata={"collection": "docs"},
            score=2.0,
            score_kind="similarity",
        )
    ]
    assert store.get_by_ids(["chunk-2"])[0].text == "beta"
    assert store.delete_by_metadata({"collection": "docs"}) == 1
    assert store.get_by_ids(["chunk-1"]) == []


def test_delete_by_metadata_is_selective_idempotent_and_reports_count() -> None:
    store = FakeVectorStore()
    store.upsert(
        [
            ChunkRecord(
                id="docs-v1",
                text="old",
                metadata={"collection": "docs", "generation": 1},
            ),
            ChunkRecord(
                id="docs-v2",
                text="active",
                metadata={"collection": "docs", "generation": 2},
            ),
            ChunkRecord(
                id="notes-v2",
                text="other collection",
                metadata={"collection": "notes", "generation": 2},
            ),
        ]
    )

    assert store.delete_by_metadata({"collection": "docs", "generation": 2}) == 1
    assert [record.id for record in store.get_by_ids(["docs-v1", "docs-v2", "notes-v2"])] == [
        "docs-v1",
        "notes-v2",
    ]
    assert store.delete_by_metadata({"collection": "docs", "generation": 2}) == 0
    assert store.delete_by_metadata({"collection": "missing"}) == 0


def test_delete_by_metadata_rejects_empty_filter_before_mutation() -> None:
    store = FakeVectorStore()
    record = ChunkRecord(id="keep", text="keep", metadata={"collection": "docs"})
    store.upsert([record])

    with pytest.raises(ValueError, match="filters must not be empty"):
        store.delete_by_metadata({})

    assert store.get_by_ids(["keep"]) == [record]


def test_create_vector_store_accepts_vector_store_config_mapping() -> None:
    VectorStoreFactory.register("fake", lambda config: FakeVectorStore())

    store = create_vector_store({"backend": "fake"})

    assert isinstance(store, BaseVectorStore)


def test_vector_store_factory_names_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unsupported VectorStore backend: missing"):
        VectorStoreFactory.create({"vector_store": {"backend": "missing"}})


def test_vector_store_factory_requires_backend() -> None:
    with pytest.raises(ValueError, match=r"vector_store\.backend"):
        VectorStoreFactory.create({"vector_store": {}})


def test_vector_store_factory_rejects_empty_registered_name() -> None:
    with pytest.raises(ValueError, match="backend name must not be empty"):
        VectorStoreFactory.register("   ", lambda config: FakeVectorStore())
