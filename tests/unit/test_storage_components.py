"""Tests for C11 + C12 + C13: BM25IndexStore, VectorUpserter, ImageStorage."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.types import Chunk, ChunkRecord, ImageRef, SearchHit
from src.ingestion.storage.bm25_indexer import BM25IndexStore
from src.ingestion.storage.image_storage import ImageStorage
from src.ingestion.storage.vector_upserter import VectorUpserter
from src.ports.ingestion import BaseVectorStore


def _chunk(text: str, cid: str, collection: str = "docs", source: str = "a.pdf") -> Chunk:
    return Chunk(
        id=cid,
        text=text,
        metadata={"source_path": source, "collection": collection},
        source_ref=cid.split(":")[0],
        chunk_index=0,
    )


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------


def test_bm25_returns_relevant_docs_first() -> None:
    idx = BM25IndexStore()
    idx.upsert(
        [
            _chunk("the quick brown fox jumps", "a"),
            _chunk("a fast red car races", "b"),
            _chunk("lazy dogs sleep all day", "c"),
        ]
    )
    hits = idx.query(["fox"], top_k=3)
    assert [h.id for h in hits] == ["a"]


def test_bm25_metadata_filters_narrow_results() -> None:
    idx = BM25IndexStore()
    idx.upsert(
        [
            _chunk("bm25 keyword retrieval", "a", collection="docs"),
            _chunk("bm25 keyword retrieval", "b", collection="other"),
        ]
    )
    hits = idx.query(["keyword"], top_k=5, filters={"collection": "docs"})
    assert [h.id for h in hits] == ["a"]


def test_bm25_remove_document_drops_postings() -> None:
    idx = BM25IndexStore()
    idx.upsert(
        [
            _chunk("alpha beta", "a", source="a.pdf"),
            _chunk("alpha gamma", "b", source="b.pdf"),
        ]
    )
    idx.remove_document("a.pdf", "docs")
    hits = idx.query(["alpha"], top_k=5)
    assert [h.id for h in hits] == ["b"]


def test_bm25_query_empty_keywords_returns_empty() -> None:
    idx = BM25IndexStore()
    idx.upsert([_chunk("anything", "a")])
    assert idx.query([], top_k=5) == []


def test_bm25_persistence_round_trip(tmp_path: Path) -> None:
    idx = BM25IndexStore()
    idx.upsert([_chunk("cat sat mat", "a"), _chunk("dog ran park", "b")])
    path = tmp_path / "bm25.json"
    idx.save(path)
    fresh = BM25IndexStore()
    fresh.load(path)
    hits = fresh.query(["cat"], top_k=5)
    assert [h.id for h in hits] == ["a"]


# ---------------------------------------------------------------------------
# VectorUpserter
# ---------------------------------------------------------------------------


class _RecordingVectorStore(BaseVectorStore):
    def __init__(self) -> None:
        self.records: list[ChunkRecord] = []

    def upsert(self, records, trace=None):
        self.records = list(records)

    def query(self, vector, top_k, filters=None, trace=None):
        return []

    def get_by_ids(self, ids):
        return []

    def delete_by_metadata(self, filters):
        return 0


def test_vector_upserter_stitches_chunk_dense_and_sparse() -> None:
    store = _RecordingVectorStore()
    chunks = [
        _chunk("alpha", "a"),
        _chunk("beta", "b"),
    ]
    dense = [[0.1, 0.2], [0.3, 0.4]]
    sparse = [{"terms": {"alpha": 1}}, {"terms": {"beta": 1}}]
    VectorUpserter(store).upsert(chunks, dense, sparse)

    assert len(store.records) == 2
    assert store.records[0].dense_vector == [0.1, 0.2]
    assert store.records[0].sparse_vector == {"terms": {"alpha": 1}}
    assert store.records[0].id == "a"


def test_vector_upserter_handles_missing_sparse_vectors() -> None:
    store = _RecordingVectorStore()
    VectorUpserter(store).upsert([_chunk("x", "x")], [[0.0]])
    assert store.records[0].dense_vector == [0.0]
    assert store.records[0].sparse_vector is None


# ---------------------------------------------------------------------------
# ImageStorage
# ---------------------------------------------------------------------------


def test_image_storage_save_and_retrieve() -> None:
    storage = ImageStorage()
    storage.save_refs(
        [
            ImageRef(image_id="i1", path="p1", collection="docs", source_path="a.pdf"),
            ImageRef(image_id="i2", path="p2", collection="docs", source_path="a.pdf"),
        ]
    )
    assert storage.get("i1").path == "p1"
    assert storage.get("missing") is None


def test_image_storage_list_by_document() -> None:
    storage = ImageStorage()
    storage.save_refs(
        [
            ImageRef(image_id="i1", path="p", collection="docs", source_path="a.pdf"),
            ImageRef(image_id="i2", path="p", collection="other", source_path="a.pdf"),
        ]
    )
    images = storage.list_by_document("a.pdf", "docs")
    assert len(images) == 1
    assert images[0].image_id == "i1"


def test_image_storage_delete_by_document() -> None:
    storage = ImageStorage()
    storage.save_refs(
        [
            ImageRef(image_id="i1", path="p", collection="docs", source_path="a.pdf"),
            ImageRef(image_id="i2", path="p", collection="docs", source_path="b.pdf"),
        ]
    )
    deleted = storage.delete_by_document("a.pdf", "docs")
    assert deleted == 1
    assert storage.get("i1") is None
    assert storage.get("i2") is not None