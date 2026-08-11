from __future__ import annotations

import pytest

from core.types import ChunkRecord, ImageRef, JsonDict
from ingestion import DocumentManager


class FakeChromaStore:
    def __init__(self) -> None:
        self.records = [
            ChunkRecord(
                id="chunk-a-1",
                text="Alpha one",
                metadata={
                    "doc_key": "doc-a",
                    "generation": 2,
                    "source_path": "/documents/a.pdf",
                    "collection": "docs",
                    "chunk_index": 0,
                },
                content_hash="hash-a-1",
            ),
            ChunkRecord(
                id="chunk-a-2",
                text="Alpha two",
                metadata={
                    "doc_key": "doc-a",
                    "generation": 2,
                    "source_path": "/documents/a.pdf",
                    "collection": "docs",
                    "chunk_index": 1,
                },
                content_hash="hash-a-2",
            ),
            ChunkRecord(
                id="chunk-b-1",
                text="Beta",
                metadata={
                    "doc_key": "doc-b",
                    "generation": 1,
                    "source_path": "/documents/b.pdf",
                    "collection": "notes",
                    "chunk_index": 0,
                },
            ),
        ]
        self.delete_calls: list[JsonDict] = []
        self.fail_delete = False

    def get_by_metadata(self, filters: JsonDict) -> list[ChunkRecord]:
        return [
            record
            for record in self.records
            if all(record.metadata.get(key) == value for key, value in filters.items())
        ]

    def delete_by_metadata(self, filters: JsonDict) -> int:
        self.delete_calls.append(dict(filters))
        if self.fail_delete:
            raise RuntimeError("vector unavailable")
        retained = [
            record
            for record in self.records
            if not all(record.metadata.get(key) == value for key, value in filters.items())
        ]
        removed = len(self.records) - len(retained)
        self.records = retained
        return removed


class FakeBM25Indexer:
    def __init__(self) -> None:
        self.remove_calls: list[tuple[str, str]] = []
        self.records: list[ChunkRecord] = []

    def list_active_chunk_records(self, collection: str) -> list[ChunkRecord]:
        return [
            record
            for record in self.records
            if record.metadata.get("collection") == collection
        ]

    def remove_document(self, source_path: str, collection: str) -> None:
        self.remove_calls.append((source_path, collection))


class FakeImageStorage:
    def __init__(self) -> None:
        self.images = {
            ("/documents/a.pdf", "docs"): [
                ImageRef(
                    image_id="image-a",
                    path="/tmp/image-a.png",
                    collection="docs",
                    source_path="/documents/a.pdf",
                )
            ],
            ("/documents/b.pdf", "notes"): [],
        }
        self.delete_calls: list[tuple[str, str]] = []

    def list_by_document(self, source_path: str, collection: str) -> list[ImageRef]:
        return list(self.images.get((source_path, collection), []))

    def delete_by_document(self, source_path: str, collection: str) -> int:
        self.delete_calls.append((source_path, collection))
        return len(self.images.pop((source_path, collection), []))


class FakeGrepIndex:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.remove_calls: list[tuple[str, str]] = []

    def remove_document(self, source_path: str, collection: str) -> int:
        self.remove_calls.append((source_path, collection))
        if self.fail:
            raise RuntimeError("index unavailable")
        return 2


class FakeIntegrityStore:
    def __init__(self) -> None:
        self.active = {"doc-a": 2, "doc-b": 1}
        self.rows: list[JsonDict] = [
            _row("doc-a", 1, "/documents/a.pdf", "docs", chunks=1),
            _row("doc-a", 2, "/documents/a.pdf", "docs", chunks=2),
            _row("doc-b", 1, "/documents/b.pdf", "notes", chunks=1),
        ]
        self.remove_calls: list[tuple[str, str | None]] = []

    def get_active_generations(self, collection: str | None = None) -> dict[str, int]:
        if collection is None:
            return dict(self.active)
        doc_keys = {str(row["doc_key"]) for row in self.rows if row.get("collection") == collection}
        return {key: value for key, value in self.active.items() if key in doc_keys}

    def list_processed(self, collection: str | None = None) -> list[JsonDict]:
        rows = self.rows
        if collection is not None:
            rows = [row for row in rows if row.get("collection") == collection]
        return list(reversed(rows))

    def remove_record(
        self,
        source_path_or_revision: str,
        collection: str | None = None,
    ) -> bool:
        self.remove_calls.append((source_path_or_revision, collection))
        matched = [
            row
            for row in self.rows
            if row.get("file_path") == source_path_or_revision
            and row.get("collection") == collection
        ]
        self.rows = [row for row in self.rows if row not in matched]
        for row in matched:
            self.active.pop(str(row["doc_key"]), None)
        return bool(matched)


def test_list_documents_returns_only_active_versions_with_counts() -> None:
    manager, _, _, _, _ = _manager()

    documents = manager.list_documents("docs")

    assert len(documents) == 1
    assert documents[0].doc_id == "doc-a"
    assert documents[0].source_path == "/documents/a.pdf"
    assert documents[0].title == "a"
    assert documents[0].metadata["generation"] == 2
    assert documents[0].metadata["chunk_count"] == 2
    assert documents[0].metadata["image_count"] == 1


def test_get_document_detail_returns_ordered_chunks_and_images() -> None:
    manager, _, _, _, _ = _manager()

    detail = manager.get_document_detail("revision-a-2")

    assert detail["document"]["doc_id"] == "doc-a"
    assert [chunk["id"] for chunk in detail["chunks"]] == ["chunk-a-1", "chunk-a-2"]
    assert detail["chunks"][0]["text"] == "Alpha one"
    assert detail["images"][0]["image_id"] == "image-a"


def test_get_document_detail_reads_bm25_when_dense_index_is_disabled() -> None:
    manager, chroma, bm25, _, _ = _manager()
    bm25.records = list(chroma.records)
    chroma.records = []

    detail = manager.get_document_detail("revision-a-2")

    assert [chunk["id"] for chunk in detail["chunks"]] == ["chunk-a-1", "chunk-a-2"]


def test_delete_document_coordinates_all_stores_and_removes_listing() -> None:
    manager, chroma, bm25, images, integrity = _manager()

    result = manager.delete_document("/documents/a.pdf", "docs")

    assert result.deleted_chunks == 2
    assert result.deleted_images == 1
    assert result.removed_bm25 is True
    assert result.removed_integrity_record is True
    assert result.errors == []
    assert chroma.delete_calls == [{"source_path": "/documents/a.pdf", "collection": "docs"}]
    assert bm25.remove_calls == [("/documents/a.pdf", "docs")]
    assert images.delete_calls == [("/documents/a.pdf", "docs")]
    assert integrity.remove_calls == [("/documents/a.pdf", "docs")]
    assert manager.list_documents("docs") == []


def test_partial_delete_keeps_integrity_record_for_retry() -> None:
    manager, chroma, bm25, images, integrity = _manager()
    chroma.fail_delete = True

    result = manager.delete_document("/documents/a.pdf", "docs")

    assert result.deleted_chunks == 0
    assert result.deleted_images == 1
    assert result.removed_bm25 is True
    assert result.removed_integrity_record is False
    assert result.errors == ["chroma: vector unavailable"]
    assert bm25.remove_calls == [("/documents/a.pdf", "docs")]
    assert images.delete_calls == [("/documents/a.pdf", "docs")]
    assert integrity.remove_calls == []


def test_grep_delete_failure_keeps_integrity_record_for_retry() -> None:
    chroma = FakeChromaStore()
    bm25 = FakeBM25Indexer()
    images = FakeImageStorage()
    integrity = FakeIntegrityStore()
    grep = FakeGrepIndex(fail=True)
    manager = DocumentManager(  # type: ignore[arg-type]
        chroma, bm25, images, integrity, grep
    )

    result = manager.delete_document("/documents/a.pdf", "docs")

    assert result.errors == ["grep: index unavailable"]
    assert grep.remove_calls == [("/documents/a.pdf", "docs")]
    assert integrity.remove_calls == []


def test_grep_document_delete_is_idempotent() -> None:
    chroma = FakeChromaStore()
    bm25 = FakeBM25Indexer()
    images = FakeImageStorage()
    integrity = FakeIntegrityStore()
    grep = FakeGrepIndex()
    manager = DocumentManager(  # type: ignore[arg-type]
        chroma, bm25, images, integrity, grep
    )

    first = manager.delete_document("/documents/a.pdf", "docs")
    repeated = manager.delete_document("/documents/a.pdf", "docs")

    assert first.removed_integrity_record is True
    assert repeated.errors == []
    assert grep.remove_calls == [
        ("/documents/a.pdf", "docs"),
        ("/documents/a.pdf", "docs"),
    ]


def test_collection_stats_aggregate_active_documents() -> None:
    manager, _, _, _, _ = _manager()

    stats = manager.get_collection_stats()

    assert stats.name == "all"
    assert stats.document_count == 2
    assert stats.chunk_count == 3
    assert stats.image_count == 1
    assert stats.metadata == {"collections": ["docs", "notes"]}


def test_delete_rejects_unsafe_collection_before_touching_stores() -> None:
    manager, chroma, bm25, images, integrity = _manager()

    with pytest.raises(ValueError, match="simple name"):
        manager.delete_document("/documents/a.pdf", "../docs")

    assert chroma.delete_calls == []
    assert bm25.remove_calls == []
    assert images.delete_calls == []
    assert integrity.remove_calls == []


def _manager() -> tuple[
    DocumentManager,
    FakeChromaStore,
    FakeBM25Indexer,
    FakeImageStorage,
    FakeIntegrityStore,
]:
    chroma = FakeChromaStore()
    bm25 = FakeBM25Indexer()
    images = FakeImageStorage()
    integrity = FakeIntegrityStore()
    manager = DocumentManager(chroma, bm25, images, integrity)  # type: ignore[arg-type]
    return manager, chroma, bm25, images, integrity


def _row(
    doc_key: str,
    generation: int,
    source_path: str,
    collection: str,
    *,
    chunks: int,
) -> JsonDict:
    return {
        "doc_key": doc_key,
        "generation": generation,
        "file_hash": f"revision-{doc_key[-1]}-{generation}",
        "file_path": source_path,
        "file_size": 100,
        "collection": collection,
        "status": "success",
        "attempt_status": "published",
        "processed_at": f"2026-08-01T00:00:0{generation}",
        "chunk_count": chunks,
    }
