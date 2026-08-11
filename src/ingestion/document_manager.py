"""Cross-store document lifecycle management."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.core.types import CollectionInfo, DeleteResult, DocumentSummary, JsonDict
from src.ingestion.storage.sqlite_grep_index import SQLiteGrepIndex
from src.libs.loader.file_integrity import normalize_source_path
from src.ports.ingestion import (
    BM25IndexStore,
    DocumentVectorStore,
    FileIntegrityStore,
    ImageStore,
)


class DocumentManager:
    """Coordinate document reads and idempotent deletion across configured stores."""

    def __init__(
        self,
        chroma_store: DocumentVectorStore,
        bm25_indexer: BM25IndexStore,
        image_storage: ImageStore,
        file_integrity: FileIntegrityStore,
        grep_index: SQLiteGrepIndex | None = None,
    ) -> None:
        self.chroma_store = chroma_store
        self.bm25_indexer = bm25_indexer
        self.image_storage = image_storage
        self.file_integrity = file_integrity
        self.grep_index = grep_index

    def list_documents(self, collection: str | None = None) -> list[DocumentSummary]:
        """List active documents with counts from their authoritative stores."""
        return [self._summary(row) for row in self._active_records(collection)]

    def get_document_detail(self, doc_id: str) -> JsonDict:
        """Return the active document, ordered Chunk content, metadata and images."""
        normalized_id = doc_id.strip()
        if not normalized_id:
            raise ValueError("doc_id must not be empty")
        row = next(
            (
                item
                for item in self._active_records()
                if normalized_id in {str(item.get("doc_key", "")), str(item.get("file_hash", ""))}
            ),
            None,
        )
        if row is None:
            raise KeyError(f"document not found: {normalized_id}")

        source_path = _required_text(row, "file_path")
        collection = _required_text(row, "collection")
        images = self.image_storage.list_by_document(source_path, collection)
        summary = self._summary(row, image_count=len(images))
        generation = _nonnegative_int(row, "generation")
        filters = {"doc_key": summary.doc_id, "generation": generation}
        chunks = self.chroma_store.get_by_metadata(filters)
        if not chunks:
            chunks = [
                chunk
                for chunk in self.bm25_indexer.list_active_chunk_records(collection)
                if all(chunk.metadata.get(key) == value for key, value in filters.items())
            ]
        return {
            "document": summary.to_dict(),
            "chunks": [
                {
                    "id": chunk.id,
                    "text": chunk.text,
                    "metadata": dict(chunk.metadata),
                    "content_hash": chunk.content_hash,
                }
                for chunk in chunks
            ],
            "images": [image.to_dict() for image in images],
        }

    def delete_document(self, source_path: str, collection: str) -> DeleteResult:
        """Best-effort delete external data, removing control state only after success."""
        normalized_path = normalize_source_path(source_path)
        if (
            not isinstance(collection, str)
            or not collection.strip()
            or Path(collection).name != collection
            or collection in {".", ".."}
        ):
            raise ValueError("collection must be a simple name")

        deleted_chunks = 0
        deleted_images = 0
        removed_bm25 = False
        removed_integrity_record = False
        errors: list[str] = []

        try:
            deleted_chunks = self.chroma_store.delete_by_metadata(
                {"source_path": normalized_path, "collection": collection}
            )
        except Exception as exc:
            errors.append(_store_error("chroma", exc))
        try:
            self.bm25_indexer.remove_document(normalized_path, collection)
            removed_bm25 = True
        except Exception as exc:
            errors.append(_store_error("bm25", exc))
        if self.grep_index is not None:
            try:
                self.grep_index.remove_document(normalized_path, collection)
            except Exception as exc:
                errors.append(_store_error("grep", exc))
        try:
            deleted_images = self.image_storage.delete_by_document(normalized_path, collection)
        except Exception as exc:
            errors.append(_store_error("images", exc))

        # The control-plane record remains as a retry anchor if any external cleanup failed.
        if not errors:
            try:
                removed_integrity_record = self.file_integrity.remove_record(
                    normalized_path, collection
                )
            except Exception as exc:
                errors.append(_store_error("integrity", exc))

        return DeleteResult(
            source_path=normalized_path,
            collection=collection,
            deleted_chunks=deleted_chunks,
            deleted_images=deleted_images,
            removed_bm25=removed_bm25,
            removed_integrity_record=removed_integrity_record,
            errors=errors,
        )

    def get_collection_stats(self, collection: str | None = None) -> CollectionInfo:
        """Aggregate active document, Chunk and image counts."""
        documents = self.list_documents(collection)
        collections = sorted(
            {
                str(document.metadata.get("collection"))
                for document in documents
                if document.metadata.get("collection")
            }
        )
        return CollectionInfo(
            name=collection or "all",
            document_count=len(documents),
            chunk_count=sum(
                _nonnegative_value(item.metadata.get("chunk_count")) for item in documents
            ),
            image_count=sum(
                _nonnegative_value(item.metadata.get("image_count")) for item in documents
            ),
            metadata={"collections": collections},
        )

    def _active_records(self, collection: str | None = None) -> list[JsonDict]:
        active_generations = self.file_integrity.get_active_generations(collection)
        return [
            row
            for row in self.file_integrity.list_processed(collection)
            if row.get("attempt_status") == "published"
            and active_generations.get(str(row.get("doc_key", ""))) == row.get("generation")
        ]

    def _summary(
        self,
        row: Mapping[str, Any],
        *,
        image_count: int | None = None,
    ) -> DocumentSummary:
        source_path = _required_text(row, "file_path")
        collection = _required_text(row, "collection")
        if image_count is None:
            image_count = len(self.image_storage.list_by_document(source_path, collection))
        return DocumentSummary(
            doc_id=_required_text(row, "doc_key"),
            source_path=source_path,
            title=Path(source_path).stem,
            metadata={
                "collection": collection,
                "source_revision": _required_text(row, "file_hash"),
                "generation": _nonnegative_int(row, "generation"),
                "chunk_count": _nonnegative_int(row, "chunk_count"),
                "image_count": image_count,
                "processed_at": row.get("processed_at"),
                "file_size": row.get("file_size"),
            },
        )


def _required_text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"invalid ingestion history field: {key}")
    return value


def _nonnegative_int(row: Mapping[str, Any], key: str) -> int:
    return _nonnegative_value(row.get(key))


def _nonnegative_value(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _store_error(store: str, exc: Exception) -> str:
    reason = str(exc) or type(exc).__name__
    return f"{store}: {reason}"


__all__ = ["DocumentManager"]
