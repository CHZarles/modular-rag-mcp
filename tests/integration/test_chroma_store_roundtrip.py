from __future__ import annotations

from pathlib import Path

import pytest

from core.types import ChunkRecord
from libs.vector_store import ChromaStore, VectorStoreFactory

pytestmark = pytest.mark.integration


def make_config(path: Path) -> dict[str, object]:
    return {
        "backend": "chroma",
        "persist_path": str(path),
        "collection_name": "roundtrip",
        "distance_metric": "cosine",
    }


def test_factory_creates_chroma_store(tmp_path: Path) -> None:
    store = VectorStoreFactory.create({"vector_store": make_config(tmp_path / "chroma")})

    assert isinstance(store, ChromaStore)


def test_chroma_upsert_query_filter_and_persistence(tmp_path: Path) -> None:
    config = make_config(tmp_path / "chroma")
    store = ChromaStore(config)
    store.upsert(
        [
            ChunkRecord(
                id="python",
                text="Python supports type annotations.",
                metadata={"collection": "docs", "tags": ["python", "typing"]},
                dense_vector=[1.0, 0.0],
                sparse_vector={"python": 1.0},
                content_hash="hash-python",
            ),
            ChunkRecord(
                id="database",
                text="Chroma stores dense vectors.",
                metadata={"collection": "docs"},
                dense_vector=[0.0, 1.0],
            ),
            ChunkRecord(
                id="notes",
                text="Private notes about Python.",
                metadata={"collection": "notes"},
                dense_vector=[0.9, 0.1],
            ),
        ]
    )

    hits = store.query([1.0, 0.0], top_k=2)
    filtered = store.query([1.0, 0.0], top_k=3, filters={"collection": "notes"})
    reopened = ChromaStore(config)
    restored = reopened.get_by_ids(["database", "python"])

    assert len(hits) == 2
    assert hits[0].id == "python"
    assert hits[0].text == "Python supports type annotations."
    assert hits[0].score_kind == "distance"
    assert [hit.id for hit in filtered] == ["notes"]
    assert [record.id for record in restored] == ["database", "python"]
    assert restored[1].metadata["tags"] == ["python", "typing"]
    assert restored[1].sparse_vector == {"python": 1.0}
    assert restored[1].content_hash == "hash-python"


def test_chroma_upsert_is_idempotent_and_delete_returns_count(tmp_path: Path) -> None:
    store = ChromaStore(make_config(tmp_path / "chroma"))
    original = ChunkRecord(
        id="same-id",
        text="old text",
        metadata={"collection": "docs"},
        dense_vector=[1.0, 0.0],
    )
    updated = ChunkRecord(
        id="same-id",
        text="new text",
        metadata={"collection": "docs"},
        dense_vector=[0.9, 0.1],
    )

    store.upsert([original])
    store.upsert([updated])

    assert store.get_by_ids(["same-id"])[0].text == "new text"
    assert store.delete_by_metadata({"collection": "docs"}) == 1
    assert store.get_by_ids(["same-id"]) == []


def test_chroma_collection_stats_deduplicate_documents_and_images(tmp_path: Path) -> None:
    store = ChromaStore(make_config(tmp_path / "chroma"))
    store.upsert(
        [
            ChunkRecord(
                id="docs-a-1",
                text="alpha",
                metadata={
                    "collection": "docs",
                    "doc_key": "doc-a",
                    "image_refs": ["image-1"],
                },
                dense_vector=[1.0, 0.0],
            ),
            ChunkRecord(
                id="docs-a-2",
                text="beta",
                metadata={
                    "collection": "docs",
                    "doc_key": "doc-a",
                    "image_refs": ["image-1", "image-2"],
                },
                dense_vector=[0.9, 0.1],
            ),
            ChunkRecord(
                id="notes-b-1",
                text="gamma",
                metadata={"collection": "notes", "doc_key": "doc-b"},
                dense_vector=[0.0, 1.0],
            ),
        ]
    )

    all_stats = store.get_collection_stats()
    docs_stats = store.get_collection_stats("docs")

    assert all_stats.name == "roundtrip"
    assert all_stats.document_count == 2
    assert all_stats.chunk_count == 3
    assert all_stats.image_count == 2
    assert docs_stats.name == "docs"
    assert docs_stats.document_count == 1
    assert docs_stats.chunk_count == 2
    assert docs_stats.image_count == 2
