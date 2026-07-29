from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import pytest

from core.types import Chunk, ChunkRecord, JsonDict, SearchHit
from ingestion.storage import VectorUpserter


class RecordingVectorStore:
    def __init__(self) -> None:
        self.calls: list[tuple[list[ChunkRecord], Any | None]] = []
        self.records: dict[str, ChunkRecord] = {}

    def upsert(self, records: list[ChunkRecord], trace: Any | None = None) -> None:
        self.calls.append((list(records), trace))
        for record in records:
            self.records[record.id] = record

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        return []

    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]:
        return [self.records[record_id] for record_id in ids if record_id in self.records]

    def delete_by_metadata(self, filters: JsonDict) -> int:
        return 0


def make_chunk(index: int, text: str, source_path: str = "docs/guide.pdf") -> Chunk:
    return Chunk(
        id=f"split-id-{index}",
        text=text,
        metadata={"source_path": source_path, "collection": "docs"},
        source_ref="document",
        chunk_index=index,
    )


def expected_storage_id(chunk: Chunk) -> str:
    content_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
    identity = f"{chunk.metadata['source_path']}\0{chunk.chunk_index}\0{content_hash[:8]}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def test_same_chunk_upserts_with_the_same_id_without_duplicate_records() -> None:
    store = RecordingVectorStore()
    upserter = VectorUpserter(store)
    chunk = make_chunk(0, "Stable content")

    first = upserter.upsert([chunk], [[0.1, 0.2]])
    second = upserter.upsert([chunk], [[0.1, 0.2]])

    assert first[0].id == expected_storage_id(chunk)
    assert second[0].id == first[0].id
    assert list(store.records) == [first[0].id]


def test_content_change_produces_new_id_and_full_content_hash() -> None:
    store = RecordingVectorStore()
    upserter = VectorUpserter(store)
    original = make_chunk(0, "Original content")
    changed = replace(original, text="Changed content")

    original_record = upserter.upsert([original], [[0.1]])[0]
    changed_record = upserter.upsert([changed], [[0.2]])[0]

    assert original_record.id != changed_record.id
    assert changed_record.id == expected_storage_id(changed)
    assert changed_record.content_hash == hashlib.sha256(changed.text.encode("utf-8")).hexdigest()
    assert changed.id == original.id


def test_batch_upsert_preserves_order_alignment_metadata_and_trace() -> None:
    store = RecordingVectorStore()
    upserter = VectorUpserter(store)
    chunks = [make_chunk(0, "Alpha"), make_chunk(1, "Beta")]
    dense_vectors = [[0.1, 0.2], [0.3, 0.4]]
    sparse_vectors = [
        {"terms": {"alpha": 1}, "doc_length": 1},
        {"terms": {"beta": 1}, "doc_length": 1},
    ]
    trace = object()

    records = upserter.upsert(
        chunks,
        dense_vectors,
        sparse_vectors=sparse_vectors,
        trace=trace,
    )

    assert [record.id for record in records] == [expected_storage_id(chunk) for chunk in chunks]
    assert [record.text for record in records] == ["Alpha", "Beta"]
    assert [record.dense_vector for record in records] == dense_vectors
    assert [record.sparse_vector for record in records] == sparse_vectors
    assert [record.metadata for record in records] == [chunk.metadata for chunk in chunks]
    assert records[0].metadata is not chunks[0].metadata
    assert store.calls == [(records, trace)]


def test_empty_batch_does_not_call_vector_store() -> None:
    store = RecordingVectorStore()

    assert VectorUpserter(store).upsert([], []) == []
    assert store.calls == []


def test_rejects_misaligned_outputs_before_writing() -> None:
    store = RecordingVectorStore()
    upserter = VectorUpserter(store)
    chunk = make_chunk(0, "Alpha")
    second = make_chunk(1, "Beta")

    with pytest.raises(ValueError, match="dense vector count must match chunk count"):
        upserter.upsert([chunk], [])
    with pytest.raises(ValueError, match="sparse vector count must match chunk count"):
        upserter.upsert([chunk], [[0.1]], sparse_vectors=[])
    with pytest.raises(ValueError, match="same dimension"):
        upserter.upsert([chunk, second], [[0.1], [0.2, 0.3]])
    with pytest.raises(ValueError, match="duplicate storage id"):
        upserter.upsert([chunk, chunk], [[0.1], [0.1]])
    assert store.calls == []


@pytest.mark.parametrize(
    ("chunk", "vector", "message"),
    [
        (make_chunk(0, "Alpha", source_path=""), [0.1], "source_path must be non-empty"),
        (make_chunk(-1, "Alpha"), [0.1], "chunk_index must be non-negative"),
        (make_chunk(0, "Alpha"), [], "dense vector must be non-empty"),
        (make_chunk(0, "Alpha"), [0.1, float("nan")], "dense vector must be finite"),
    ],
)
def test_rejects_invalid_chunk_identity_or_vector(
    chunk: Chunk,
    vector: list[float],
    message: str,
) -> None:
    store = RecordingVectorStore()

    with pytest.raises(ValueError, match=message):
        VectorUpserter(store).upsert([chunk], [vector])
    assert store.calls == []
